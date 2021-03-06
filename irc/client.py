# -*- coding: utf-8 -*-

# Copyright (C) 1999-2002  Joel Rosdahl
# Copyright © 2011-2012 Jason R. Coombs
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307  USA
#
# keltus <keltus@users.sourceforge.net>

"""
Internet Relay Chat (IRC) protocol client library.

This library is intended to encapsulate the IRC protocol at a quite
low level.  It provides an event-driven IRC client framework.  It has
a fairly thorough support for the basic IRC protocol, CTCP, DCC chat,
but DCC file transfers is not yet supported.

In order to understand how to make an IRC client, I'm afraid you more
or less must understand the IRC specifications.  They are available
here: [IRC specifications].

The main features of the IRC client framework are:

  * Abstraction of the IRC protocol.
  * Handles multiple simultaneous IRC server connections.
  * Handles server PONGing transparently.
  * Messages to the IRC server are done by calling methods on an IRC
    connection object.
  * Messages from an IRC server triggers events, which can be caught
    by event handlers.
  * Reading from and writing to IRC server sockets are normally done
    by an internal select() loop, but the select()ing may be done by
    an external main loop.
  * Functions can be registered to execute at specified times by the
    event-loop.
  * Decodes CTCP tagging correctly (hopefully); I haven't seen any
    other IRC client implementation that handles the CTCP
    specification subtilties.
  * A kind of simple, single-server, object-oriented IRC client class
    that dispatches events to instance methods is included.

Current limitations:

  * The IRC protocol shines through the abstraction a bit too much.
  * Data is not written asynchronously to the server, i.e. the write()
    may block if the TCP buffers are stuffed.
  * There are no support for DCC file transfers.
  * The author haven't even read RFC 2810, 2811, 2812 and 2813.
  * Like most projects, documentation is lacking...

.. [IRC specifications] http://www.irchelp.org/irchelp/rfc/
"""

from __future__ import absolute_import

import bisect
import re
import select
import socket
import string
import time
import types
import ssl as ssl_mod
import datetime
import struct
import logging
import itertools

try:
    import pkg_resources
except ImportError:
    pass

from . import functools as irc_functools
from . import events
from . import util
from . import strings
from . import modes

log = logging.getLogger(__name__)

# set the version tuple
try:
    VERSION = tuple(int(res) for res in re.findall('\d+',
        pkg_resources.require('irc')[0].version))
except Exception:
    VERSION = ()

# TODO
# ----
# (maybe) thread safety
# (maybe) color parser convenience functions
# documentation (including all event types)
# (maybe) add awareness of different types of ircds
# send data asynchronously to the server (and DCC connections)
# (maybe) automatically close unused, passive DCC connections after a while

# NOTES
# -----
# connection.quit() only sends QUIT to the server.
# ERROR from the server triggers the error event and the disconnect event.
# dropping of the connection triggers the disconnect event.

class IRCError(Exception):
    "An IRC exception"


class IRC(object):
    """Class that handles one or several IRC server connections.

    When an IRC object has been instantiated, it can be used to create
    Connection objects that represent the IRC connections.  The
    responsibility of the IRC object is to provide an event-driven
    framework for the connections and to keep the connections alive.
    It runs a select loop to poll each connection's TCP socket and
    hands over the sockets with incoming data for processing by the
    corresponding connection.

    The methods of most interest for an IRC client writer are server,
    add_global_handler, remove_global_handler, execute_at,
    execute_delayed, execute_every, process_once, and process_forever.

    Here is an example:

        client = irc.client.IRC()
        server = client.server()
        server.connect("irc.some.where", 6667, "my_nickname")
        server.privmsg("a_nickname", "Hi there!")
        client.process_forever()

    This will connect to the IRC server irc.some.where on port 6667
    using the nickname my_nickname and send the message "Hi there!"
    to the nickname a_nickname.
    """

    def __init__(self, fn_to_add_socket=None,
                 fn_to_remove_socket=None,
                 fn_to_add_timeout=None):
        """Constructor for IRC objects.

        Optional arguments are fn_to_add_socket, fn_to_remove_socket
        and fn_to_add_timeout.  The first two specify functions that
        will be called with a socket object as argument when the IRC
        object wants to be notified (or stop being notified) of data
        coming on a new socket.  When new data arrives, the method
        process_data should be called.  Similarly, fn_to_add_timeout
        is called with a number of seconds (a floating point number)
        as first argument when the IRC object wants to receive a
        notification (by calling the process_timeout method).  So, if
        e.g. the argument is 42.17, the object wants the
        process_timeout method to be called after 42 seconds and 170
        milliseconds.

        The three arguments mainly exist to be able to use an external
        main loop (for example Tkinter's or PyGTK's main app loop)
        instead of calling the process_forever method.

        An alternative is to just call ServerConnection.process_once()
        once in a while.
        """

        if fn_to_add_socket and fn_to_remove_socket:
            self.fn_to_add_socket = fn_to_add_socket
            self.fn_to_remove_socket = fn_to_remove_socket
        else:
            self.fn_to_add_socket = None
            self.fn_to_remove_socket = None

        self.fn_to_add_timeout = fn_to_add_timeout
        self.connections = []
        self.handlers = {}
        self.delayed_commands = []  # list of DelayedCommands

        self.add_global_handler("ping", _ping_ponger, -42)

    def server(self):
        """Creates and returns a ServerConnection object."""

        c = ServerConnection(self)
        self.connections.append(c)
        return c

    def process_data(self, sockets):
        """Called when there is more data to read on connection sockets.

        Arguments:

            sockets -- A list of socket objects.

        See documentation for IRC.__init__.
        """
        log.log(logging.DEBUG-2, "process_data()")
        for s, c in itertools.product(sockets, self.connections):
            if s == c._get_socket():
                c.process_data()

    def process_timeout(self):
        """Called when a timeout notification is due.

        See documentation for IRC.__init__.
        """
        while self.delayed_commands:
            command = self.delayed_commands[0]
            if not command.due():
                break
            command.function(*command.arguments)
            if isinstance(command, PeriodicCommand):
                self._schedule_command(command.next())
            del self.delayed_commands[0]

    def process_once(self, timeout=0):
        """Process data from connections once.

        Arguments:

            timeout -- How long the select() call should wait if no
                       data is available.

        This method should be called periodically to check and process
        incoming data, if there are any.  If that seems boring, look
        at the process_forever method.
        """
        log.log(logging.DEBUG-2, "process_once()")
        sockets = map(lambda x: x._get_socket(), self.connections)
        sockets = filter(lambda x: x != None, sockets)
        if sockets:
            (i, o, e) = select.select(sockets, [], [], timeout)
            self.process_data(i)
        else:
            time.sleep(timeout)
        self.process_timeout()

    def process_forever(self, timeout=0.2):
        """Run an infinite loop, processing data from connections.

        This method repeatedly calls process_once.

        Arguments:

            timeout -- Parameter to pass to process_once.
        """
        log.debug("process_forever(timeout=%s)", timeout)
        while 1:
            self.process_once(timeout)

    def disconnect_all(self, message=""):
        """Disconnects all connections."""
        for c in self.connections:
            c.disconnect(message)

    def add_global_handler(self, event, handler, priority=0):
        """Adds a global handler function for a specific event type.

        Arguments:

            event -- Event type (a string).  Check the values of
            numeric_events for possible event types.

            handler -- Callback function.

            priority -- A number (the lower number, the higher priority).

        The handler function is called whenever the specified event is
        triggered in any of the connections.  See documentation for
        the Event class.

        The handler functions are called in priority order (lowest
        number is highest priority).  If a handler function returns
        "NO MORE", no more handlers will be called.
        """
        event_handlers = self.handlers.setdefault(event, [])
        bisect.insort(event_handlers, (priority, handler))

    def remove_global_handler(self, event, handler):
        """Removes a global handler function.

        Arguments:

            event -- Event type (a string).
            handler -- Callback function.

        Returns 1 on success, otherwise 0.
        """
        if not event in self.handlers:
            return 0
        for h in self.handlers[event]:
            if handler == h[1]:
                self.handlers[event].remove(h)
        return 1

    def execute_at(self, at, function, arguments=()):
        """Execute a function at a specified time.

        Arguments:

            at -- Execute at this time (standard "time_t" time).
            function -- Function to call.
            arguments -- Arguments to give the function.
        """
        command = DelayedCommand.at_time(at, function, arguments)
        self._schedule_command(command)

    def execute_delayed(self, delay, function, arguments=()):
        """
        Execute a function after a specified time.

        delay -- How many seconds to wait.
        function -- Function to call.
        arguments -- Arguments to give the function.
        """
        command = DelayedCommand(delay, function, arguments)
        self._schedule_command(command)

    def execute_every(self, period, function, arguments=()):
        """
        Execute a function every 'period' seconds.

        period -- How often to run (always waits this long for first).
        function -- Function to call.
        arguments -- Arguments to give the function.
        """
        command = PeriodicCommand(period, function, arguments)
        self._schedule_command(command)

    def _schedule_command(self, command):
        bisect.insort(self.delayed_commands, command)
        if self.fn_to_add_timeout:
            self.fn_to_add_timeout(util.total_seconds(command.delay))

    def dcc(self, dcctype="chat"):
        """Creates and returns a DCCConnection object.

        Arguments:

            dcctype -- "chat" for DCC CHAT connections or "raw" for
                       DCC SEND (or other DCC types). If "chat",
                       incoming data will be split in newline-separated
                       chunks. If "raw", incoming data is not touched.
        """
        c = DCCConnection(self, dcctype)
        self.connections.append(c)
        return c

    def _handle_event(self, connection, event):
        """[Internal]"""
        h = self.handlers
        th = sorted(h.get("all_events", []) + h.get(event.eventtype(), []))
        for handler in th:
            if handler[1](connection, event) == "NO MORE":
                return

    def _remove_connection(self, connection):
        """[Internal]"""
        self.connections.remove(connection)
        if self.fn_to_remove_socket:
            self.fn_to_remove_socket(connection._get_socket())

class DelayedCommand(datetime.datetime):
    """
    A command to be executed after some delay (seconds or timedelta).

    Clients may override .now() to have dates interpreted in a different
    manner, such as to use UTC or to have timezone-aware times.
    """
    def __new__(cls, delay, function, arguments):
        if not isinstance(delay, datetime.timedelta):
            delay = datetime.timedelta(seconds=delay)
        at = cls.now() + delay
        cmd = datetime.datetime.__new__(cls, at.year,
            at.month, at.day, at.hour, at.minute, at.second,
            at.microsecond, at.tzinfo)
        cmd.delay = delay
        cmd.function = function
        cmd.arguments = arguments
        return cmd

    @classmethod
    def now(self, tzinfo=None):
        return datetime.datetime.now(tzinfo)

    @classmethod
    def at_time(cls, at, function, arguments):
        """
        Construct a DelayedCommand to come due at `at`, where `at` may be
        a datetime or timestamp. If `at` is a timestamp, it will be
        interpreted as a naive local timestamp.
        """
        if isinstance(at, int):
            at = datetime.datetime.fromtimestamp(at)
        delay = at - cls.now()
        return cls(delay, function, arguments)

    def due(self):
        return self.now() >= self

class PeriodicCommand(DelayedCommand):
    """
    Like a deferred command, but expect this command to run every delay
    seconds.
    """
    def next(self):
        return PeriodicCommand(self.delay, self.function,
            self.arguments)

class PeriodicCommandFixedDelay(PeriodicCommand):
    """
    Like a periodic command, but don't calculate the delay based on
    the current time. Instead use a fixed delay following the initial
    run.
    """
    @classmethod
    def at_time(cls, at, delay, function, arguments):
        cmd = super(PeriodicCommandFixedDelay, cls).at_time(
            at, function, arguments)
        if not isinstance(delay, datetime.timedelta):
            delay = datetime.timedelta(seconds=delay)
        cmd.delay = delay
        return cmd

_rfc_1459_command_regexp = re.compile("^(:(?P<prefix>[^ ]+) +)?(?P<command>[^ ]+)( *(?P<argument> .+))?")

class Connection(object):
    """Base class for IRC connections.

    Must be overridden.
    """
    def __init__(self, irclibobj):
        self.irclibobj = irclibobj

    def _get_socket():
        raise IRCError("Not overridden")

    ##############################
    ### Convenience wrappers.

    def execute_at(self, at, function, arguments=()):
        self.irclibobj.execute_at(at, function, arguments)

    def execute_delayed(self, delay, function, arguments=()):
        self.irclibobj.execute_delayed(delay, function, arguments)

    def execute_every(self, period, function, arguments=()):
        self.irclibobj.execute_every(period, function, arguments)

class ServerConnectionError(IRCError):
    pass

class ServerNotConnectedError(ServerConnectionError):
    pass


# Huh!?  Crrrrazy EFNet doesn't follow the RFC: their ircd seems to
# use \n as message separator!  :P
_linesep_regexp = re.compile("\r?\n")

class ServerConnection(Connection):
    """This class represents an IRC server connection.

    ServerConnection objects are instantiated by calling the server
    method on an IRC object.
    """

    def __init__(self, irclibobj):
        super(ServerConnection, self).__init__(irclibobj)
        self.connected = False
        self.socket = None
        self.ssl = None

    # save the method args to allow for easier reconnection.
    @irc_functools.save_method_args
    def connect(self, server, port, nickname, password=None, username=None,
            ircname=None, localaddress="", localport=0, ssl=False, ipv6=False):
        """Connect/reconnect to a server.

        Arguments:

            server -- Server name.
            port -- Port number.
            nickname -- The nickname.
            password -- Password (if any).
            username -- The username.
            ircname -- The IRC name ("realname").
            localaddress -- Bind the connection to a specific local IP address.
            localport -- Bind the connection to a specific local port.
            ssl -- Enable support for ssl.
            ipv6 -- Enable support for ipv6.

        This function can be called to reconnect a closed connection.

        Returns the ServerConnection object.
        """
        log.debug("connect(server=%r, port=%r, nickname=%r, ...)", server,
            port, nickname)

        if self.connected:
            self.disconnect("Changing servers")

        self.previous_buffer = ""
        self.handlers = {}
        self.real_server_name = ""
        self.real_nickname = nickname
        self.server = server
        self.port = port
        self.nickname = nickname
        self.username = username or nickname
        self.ircname = ircname or nickname
        self.password = password
        self.localaddress = localaddress
        self.localport = localport
        self.localhost = socket.gethostname()
        self.ipv6 = ipv6
        if ipv6:
            self.socket = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        else:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.socket.bind((self.localaddress, self.localport))
            self.socket.connect((self.server, self.port))
            if ssl:
                self.ssl = ssl_mod.wrap_socket(self.socket)
        except socket.error as err:
            self.socket.close()
            self.socket = None
            raise ServerConnectionError("Couldn't connect to socket: %s" % err)
        self.connected = True
        if self.irclibobj.fn_to_add_socket:
            self.irclibobj.fn_to_add_socket(self.socket)

        # Log on...
        if self.password:
            self.pass_(self.password)
        self.nick(self.nickname)
        self.user(self.username, self.ircname)
        return self

    def reconnect(self):
        """
        Reconnect with the last arguments passed to self.connect()
        """
        self.connect(*self._saved_connect.args, **self._saved_connect.kwargs)

    def close(self):
        """Close the connection.

        This method closes the connection permanently; after it has
        been called, the object is unusable.
        """

        self.disconnect("Closing object")
        self.irclibobj._remove_connection(self)

    def _get_socket(self):
        """[Internal]"""
        return self.socket

    def get_server_name(self):
        """Get the (real) server name.

        This method returns the (real) server name, or, more
        specifically, what the server calls itself.
        """

        if self.real_server_name:
            return self.real_server_name
        else:
            return ""

    def get_nickname(self):
        """Get the (real) nick name.

        This method returns the (real) nickname.  The library keeps
        track of nick changes, so it might not be the nick name that
        was passed to the connect() method.  """

        return self.real_nickname

    def process_data(self):
        """[Internal]"""

        try:
            if self.ssl:
                new_data = self.ssl.read(2 ** 14)
            else:
                new_data = self.socket.recv(2 ** 14)
        except socket.error:
            # The server hung up.
            self.disconnect("Connection reset by peer")
            return
        if not new_data:
            # Read nothing: connection must be down.
            self.disconnect("Connection reset by peer")
            return

        lines = _linesep_regexp.split(self.previous_buffer + new_data)

        # Save the last, unfinished line.
        self.previous_buffer = lines.pop()

        for line in lines:
            log.debug("FROM SERVER: %s", line)

            if not line:
                continue

            prefix = None
            command = None
            arguments = None
            self._handle_event(Event("all_raw_messages",
                                     self.get_server_name(),
                                     None,
                                     [line]))

            m = _rfc_1459_command_regexp.match(line)
            if m.group("prefix"):
                prefix = m.group("prefix")
                if not self.real_server_name:
                    self.real_server_name = prefix

            if m.group("command"):
                command = m.group("command").lower()

            if m.group("argument"):
                a = m.group("argument").split(" :", 1)
                arguments = a[0].split()
                if len(a) == 2:
                    arguments.append(a[1])

            # Translate numerics into more readable strings.
            if command in numeric_events:
                command = numeric_events[command]

            if command == "nick":
                if NickMask(prefix).nick == self.real_nickname:
                    self.real_nickname = arguments[0]
            elif command == "welcome":
                # Record the nickname in case the client changed nick
                # in a nicknameinuse callback.
                self.real_nickname = arguments[0]

            if command in ["privmsg", "notice"]:
                target, message = arguments[0], arguments[1]
                messages = _ctcp_dequote(message)

                if command == "privmsg":
                    if is_channel(target):
                        command = "pubmsg"
                else:
                    if is_channel(target):
                        command = "pubnotice"
                    else:
                        command = "privnotice"

                for m in messages:
                    if type(m) is types.TupleType:
                        if command in ["privmsg", "pubmsg"]:
                            command = "ctcp"
                        else:
                            command = "ctcpreply"

                        m = list(m)
                        log.debug("command: %s, source: %s, target: %s, "
                            "arguments: %s", command, prefix, target, m)
                        self._handle_event(Event(command, NickMask(prefix), target, m))
                        if command == "ctcp" and m[0] == "ACTION":
                            self._handle_event(Event("action", prefix, target, m[1:]))
                    else:
                        log.debug("command: %s, source: %s, target: %s, "
                            "arguments: %s", command, prefix, target, [m])
                        self._handle_event(Event(command, NickMask(prefix), target, [m]))
            else:
                target = None

                if command == "quit":
                    arguments = [arguments[0]]
                elif command == "ping":
                    target = arguments[0]
                else:
                    target = arguments[0]
                    arguments = arguments[1:]

                if command == "mode":
                    if not is_channel(target):
                        command = "umode"

                log.debug("command: %s, source: %s, target: %s, "
                    "arguments: %s", command, prefix, target, arguments)
                self._handle_event(Event(command, NickMask(prefix), target, arguments))

    def _handle_event(self, event):
        """[Internal]"""
        self.irclibobj._handle_event(self, event)
        if event.eventtype() in self.handlers:
            for fn in self.handlers[event.eventtype()]:
                fn(self, event)

    def is_connected(self):
        """Return connection status.

        Returns true if connected, otherwise false.
        """
        return self.connected

    def add_global_handler(self, *args):
        """Add global handler.

        See documentation for IRC.add_global_handler.
        """
        self.irclibobj.add_global_handler(*args)

    def remove_global_handler(self, *args):
        """Remove global handler.

        See documentation for IRC.remove_global_handler.
        """
        self.irclibobj.remove_global_handler(*args)

    def action(self, target, action):
        """Send a CTCP ACTION command."""
        self.ctcp("ACTION", target, action)

    def admin(self, server=""):
        """Send an ADMIN command."""
        self.send_raw(" ".join(["ADMIN", server]).strip())

    def ctcp(self, ctcptype, target, parameter=""):
        """Send a CTCP command."""
        ctcptype = ctcptype.upper()
        self.privmsg(target, "\001%s%s\001" % (ctcptype, parameter and (" " + parameter) or ""))

    def ctcp_reply(self, target, parameter):
        """Send a CTCP REPLY command."""
        self.notice(target, "\001%s\001" % parameter)

    def disconnect(self, message=""):
        """Hang up the connection.

        Arguments:

            message -- Quit message.
        """
        if not self.connected:
            return

        self.connected = 0

        self.quit(message)

        try:
            self.socket.shutdown(socket.SHUT_WR)
            self.socket.close()
        except socket.error:
            pass
        self.socket = None
        self._handle_event(Event("disconnect", self.server, "", [message]))

    def globops(self, text):
        """Send a GLOBOPS command."""
        self.send_raw("GLOBOPS :" + text)

    def info(self, server=""):
        """Send an INFO command."""
        self.send_raw(" ".join(["INFO", server]).strip())

    def invite(self, nick, channel):
        """Send an INVITE command."""
        self.send_raw(" ".join(["INVITE", nick, channel]).strip())

    def ison(self, nicks):
        """Send an ISON command.

        Arguments:

            nicks -- List of nicks.
        """
        self.send_raw("ISON " + " ".join(nicks))

    def join(self, channel, key=""):
        """Send a JOIN command."""
        self.send_raw("JOIN %s%s" % (channel, (key and (" " + key))))

    def kick(self, channel, nick, comment=""):
        """Send a KICK command."""
        self.send_raw("KICK %s %s%s" % (channel, nick, (comment and (" :" + comment))))

    def links(self, remote_server="", server_mask=""):
        """Send a LINKS command."""
        command = "LINKS"
        if remote_server:
            command = command + " " + remote_server
        if server_mask:
            command = command + " " + server_mask
        self.send_raw(command)

    def list(self, channels=None, server=""):
        """Send a LIST command."""
        command = "LIST"
        if channels:
            command = command + " " + ",".join(channels)
        if server:
            command = command + " " + server
        self.send_raw(command)

    def lusers(self, server=""):
        """Send a LUSERS command."""
        self.send_raw("LUSERS" + (server and (" " + server)))

    def mode(self, target, command):
        """Send a MODE command."""
        self.send_raw("MODE %s %s" % (target, command))

    def motd(self, server=""):
        """Send an MOTD command."""
        self.send_raw("MOTD" + (server and (" " + server)))

    def names(self, channels=None):
        """Send a NAMES command."""
        self.send_raw("NAMES" + (channels and (" " + ",".join(channels)) or ""))

    def nick(self, newnick):
        """Send a NICK command."""
        self.send_raw("NICK " + newnick)

    def notice(self, target, text):
        """Send a NOTICE command."""
        # Should limit len(text) here!
        self.send_raw("NOTICE %s :%s" % (target, text))

    def oper(self, nick, password):
        """Send an OPER command."""
        self.send_raw("OPER %s %s" % (nick, password))

    def part(self, channels, message=""):
        """Send a PART command."""
        channels = util.always_iterable(channels)
        cmd_parts = [
            'PART',
            ','.join(channels),
        ]
        if message: cmd_parts.append(message)
        self.send_raw(' '.join(cmd_parts))

    def pass_(self, password):
        """Send a PASS command."""
        self.send_raw("PASS " + password)

    def ping(self, target, target2=""):
        """Send a PING command."""
        self.send_raw("PING %s%s" % (target, target2 and (" " + target2)))

    def pong(self, target, target2=""):
        """Send a PONG command."""
        self.send_raw("PONG %s%s" % (target, target2 and (" " + target2)))

    def privmsg(self, target, text):
        """Send a PRIVMSG command."""
        # Should limit len(text) here!
        self.send_raw("PRIVMSG %s :%s" % (target, text))

    def privmsg_many(self, targets, text):
        """Send a PRIVMSG command to multiple targets."""
        # Should limit len(text) here!
        self.send_raw("PRIVMSG %s :%s" % (",".join(targets), text))

    def quit(self, message=""):
        """Send a QUIT command."""
        # Note that many IRC servers don't use your QUIT message
        # unless you've been connected for at least 5 minutes!
        self.send_raw("QUIT" + (message and (" :" + message)))

    def send_raw(self, string):
        """Send raw string to the server.

        The string will be padded with appropriate CR LF.
        """
        bytes = string.encode('utf-8') + '\r\n'
        sender = self.ssl.write if self.ssl else self.socket.send
        if self.socket is None:
            raise ServerNotConnectedError("Not connected.")
        try:
            sender(bytes)
            log.debug("TO SERVER: %s", string)
        except socket.error:
            # Ouch!
            self.disconnect("Connection reset by peer.")

    def squit(self, server, comment=""):
        """Send an SQUIT command."""
        self.send_raw("SQUIT %s%s" % (server, comment and (" :" + comment)))

    def stats(self, statstype, server=""):
        """Send a STATS command."""
        self.send_raw("STATS %s%s" % (statstype, server and (" " + server)))

    def time(self, server=""):
        """Send a TIME command."""
        self.send_raw("TIME" + (server and (" " + server)))

    def topic(self, channel, new_topic=None):
        """Send a TOPIC command."""
        if new_topic is None:
            self.send_raw("TOPIC " + channel)
        else:
            self.send_raw("TOPIC %s :%s" % (channel, new_topic))

    def trace(self, target=""):
        """Send a TRACE command."""
        self.send_raw("TRACE" + (target and (" " + target)))

    def user(self, username, realname):
        """Send a USER command."""
        self.send_raw("USER %s 0 * :%s" % (username, realname))

    def userhost(self, nicks):
        """Send a USERHOST command."""
        self.send_raw("USERHOST " + ",".join(nicks))

    def users(self, server=""):
        """Send a USERS command."""
        self.send_raw("USERS" + (server and (" " + server)))

    def version(self, server=""):
        """Send a VERSION command."""
        self.send_raw("VERSION" + (server and (" " + server)))

    def wallops(self, text):
        """Send a WALLOPS command."""
        self.send_raw("WALLOPS :" + text)

    def who(self, target="", op=""):
        """Send a WHO command."""
        self.send_raw("WHO%s%s" % (target and (" " + target), op and (" o")))

    def whois(self, targets):
        """Send a WHOIS command."""
        self.send_raw("WHOIS " + ",".join(targets))

    def whowas(self, nick, max="", server=""):
        """Send a WHOWAS command."""
        self.send_raw("WHOWAS %s%s%s" % (nick,
                                         max and (" " + max),
                                         server and (" " + server)))

class DCCConnectionError(IRCError):
    pass


class DCCConnection(Connection):
    """This class represents a DCC connection.

    DCCConnection objects are instantiated by calling the dcc
    method on an IRC object.
    """
    def __init__(self, irclibobj, dcctype):
        super(DCCConnection, self).__init__(irclibobj)
        self.connected = 0
        self.passive = 0
        self.dcctype = dcctype
        self.peeraddress = None
        self.peerport = None

    def connect(self, address, port):
        """Connect/reconnect to a DCC peer.

        Arguments:
            address -- Host/IP address of the peer.

            port -- The port number to connect to.

        Returns the DCCConnection object.
        """
        self.peeraddress = socket.gethostbyname(address)
        self.peerport = port
        self.socket = None
        self.previous_buffer = ""
        self.handlers = {}
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.passive = 0
        try:
            self.socket.connect((self.peeraddress, self.peerport))
        except socket.error, x:
            raise DCCConnectionError("Couldn't connect to socket: %s" % x)
        self.connected = 1
        if self.irclibobj.fn_to_add_socket:
            self.irclibobj.fn_to_add_socket(self.socket)
        return self

    def listen(self):
        """Wait for a connection/reconnection from a DCC peer.

        Returns the DCCConnection object.

        The local IP address and port are available as
        self.localaddress and self.localport.  After connection from a
        peer, the peer address and port are available as
        self.peeraddress and self.peerport.
        """
        self.previous_buffer = ""
        self.handlers = {}
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.passive = 1
        try:
            self.socket.bind((socket.gethostbyname(socket.gethostname()), 0))
            self.localaddress, self.localport = self.socket.getsockname()
            self.socket.listen(10)
        except socket.error, x:
            raise DCCConnectionError("Couldn't bind socket: %s" % x)
        return self

    def disconnect(self, message=""):
        """Hang up the connection and close the object.

        Arguments:

            message -- Quit message.
        """
        if not self.connected:
            return

        self.connected = 0
        try:
            self.socket.shutdown(socket.SHUT_WR)
            self.socket.close()
        except socket.error:
            pass
        self.socket = None
        self.irclibobj._handle_event(
            self,
            Event("dcc_disconnect", self.peeraddress, "", [message]))
        self.irclibobj._remove_connection(self)

    def process_data(self):
        """[Internal]"""

        if self.passive and not self.connected:
            conn, (self.peeraddress, self.peerport) = self.socket.accept()
            self.socket.close()
            self.socket = conn
            self.connected = 1
            log.debug("DCC connection from %s:%d", self.peeraddress,
                self.peerport)
            self.irclibobj._handle_event(
                self,
                Event("dcc_connect", self.peeraddress, None, None))
            return

        try:
            new_data = self.socket.recv(2 ** 14)
        except socket.error:
            # The server hung up.
            self.disconnect("Connection reset by peer")
            return
        if not new_data:
            # Read nothing: connection must be down.
            self.disconnect("Connection reset by peer")
            return

        if self.dcctype == "chat":
            # The specification says lines are terminated with LF, but
            # it seems safer to handle CR LF terminations too.
            chunks = _linesep_regexp.split(self.previous_buffer + new_data)

            # Save the last, unfinished line.
            self.previous_buffer = chunks[-1]
            if len(self.previous_buffer) > 2 ** 14:
                # Bad peer! Naughty peer!
                self.disconnect()
                return
            chunks = chunks[:-1]
        else:
            chunks = [new_data]

        command = "dccmsg"
        prefix = self.peeraddress
        target = None
        for chunk in chunks:
            log.debug("FROM PEER: %s", chunk)
            arguments = [chunk]
            log.debug("command: %s, source: %s, target: %s, arguments: %s",
                command, prefix, target, arguments)
            self.irclibobj._handle_event(
                self,
                Event(command, prefix, target, arguments))

    def _get_socket(self):
        """[Internal]"""
        return self.socket

    def privmsg(self, string):
        """Send data to DCC peer.

        The string will be padded with appropriate LF if it's a DCC
        CHAT session.
        """
        bytes = string.encode('utf-8')
        try:
            self.socket.send(bytes)
            if self.dcctype == "chat":
                self.socket.send("\n")
            log.debug("TO PEER: %s\n", string)
        except socket.error:
            # Ouch!
            self.disconnect("Connection reset by peer.")

class SimpleIRCClient(object):
    """A simple single-server IRC client class.

    This is an example of an object-oriented wrapper of the IRC
    framework.  A real IRC client can be made by subclassing this
    class and adding appropriate methods.

    The method on_join will be called when a "join" event is created
    (which is done when the server sends a JOIN messsage/command),
    on_privmsg will be called for "privmsg" events, and so on.  The
    handler methods get two arguments: the connection object (same as
    self.connection) and the event object.

    Instance attributes that can be used by sub classes:

        ircobj -- The IRC instance.

        connection -- The ServerConnection instance.

        dcc_connections -- A list of DCCConnection instances.
    """
    def __init__(self):
        self.ircobj = IRC()
        self.connection = self.ircobj.server()
        self.dcc_connections = []
        self.ircobj.add_global_handler("all_events", self._dispatcher, -10)
        self.ircobj.add_global_handler("dcc_disconnect", self._dcc_disconnect, -10)

    def _dispatcher(self, c, e):
        """[Internal]"""
        log.debug("_dispatcher: %s", e.eventtype())

        m = "on_" + e.eventtype()
        if hasattr(self, m):
            getattr(self, m)(c, e)

    def _dcc_disconnect(self, c, e):
        self.dcc_connections.remove(c)

    def connect(self, server, port, nickname, password=None, username=None,
                ircname=None, localaddress="", localport=0, ssl=False, ipv6=False):
        """Connect/reconnect to a server.

        Arguments:

            server -- Server name.

            port -- Port number.

            nickname -- The nickname.

            password -- Password (if any).

            username -- The username.

            ircname -- The IRC name.

            localaddress -- Bind the connection to a specific local IP address.

            localport -- Bind the connection to a specific local port.

            ssl -- Enable support for ssl.

            ipv6 -- Enable support for ipv6.

        This function can be called to reconnect a closed connection.
        """
        self.connection.connect(server, port, nickname,
                                password, username, ircname,
                                localaddress, localport, ssl, ipv6)

    def dcc_connect(self, address, port, dcctype="chat"):
        """Connect to a DCC peer.

        Arguments:

            address -- IP address of the peer.

            port -- Port to connect to.

        Returns a DCCConnection instance.
        """
        dcc = self.ircobj.dcc(dcctype)
        self.dcc_connections.append(dcc)
        dcc.connect(address, port)
        return dcc

    def dcc_listen(self, dcctype="chat"):
        """Listen for connections from a DCC peer.

        Returns a DCCConnection instance.
        """
        dcc = self.ircobj.dcc(dcctype)
        self.dcc_connections.append(dcc)
        dcc.listen()
        return dcc

    def start(self):
        """Start the IRC client."""
        self.ircobj.process_forever()


class Event(object):
    """Class representing an IRC event."""
    def __init__(self, eventtype, source, target, arguments=None):
        """Constructor of Event objects.

        Arguments:

            eventtype -- A string describing the event.

            source -- The originator of the event (a nick mask or a server).

            target -- The target of the event (a nick or a channel).

            arguments -- Any event specific arguments.
        """
        self._eventtype = eventtype
        self._source = source
        self._target = target
        if arguments:
            self._arguments = arguments
        else:
            self._arguments = []

    def eventtype(self):
        """Get the event type."""
        return self._eventtype

    def source(self):
        """Get the event source."""
        return self._source

    def target(self):
        """Get the event target."""
        return self._target

    def arguments(self):
        """Get the event arguments."""
        return self._arguments

_LOW_LEVEL_QUOTE = "\020"
_CTCP_LEVEL_QUOTE = "\134"
_CTCP_DELIMITER = "\001"

_low_level_mapping = {
    "0": "\000",
    "n": "\n",
    "r": "\r",
    _LOW_LEVEL_QUOTE: _LOW_LEVEL_QUOTE
}

_low_level_regexp = re.compile(_LOW_LEVEL_QUOTE + "(.)")

def mask_matches(nick, mask):
    """Check if a nick matches a mask.

    Returns true if the nick matches, otherwise false.
    """
    nick = strings.lower(nick)
    mask = strings.lower(mask)
    mask = mask.replace("\\", "\\\\")
    for ch in ".$|[](){}+":
        mask = mask.replace(ch, "\\" + ch)
    mask = mask.replace("?", ".")
    mask = mask.replace("*", ".*")
    r = re.compile(mask, re.IGNORECASE)
    return r.match(nick)

_special = "-[]\\`^{}"
nick_characters = string.ascii_letters + string.digits + _special

def _ctcp_dequote(message):
    """[Internal] Dequote a message according to CTCP specifications.

    The function returns a list where each element can be either a
    string (normal message) or a tuple of one or two strings (tagged
    messages).  If a tuple has only one element (ie is a singleton),
    that element is the tag; otherwise the tuple has two elements: the
    tag and the data.

    Arguments:

        message -- The message to be decoded.
    """

    def _low_level_replace(match_obj):
        ch = match_obj.group(1)

        # If low_level_mapping doesn't have the character as key, we
        # should just return the character.
        return _low_level_mapping.get(ch, ch)

    if _LOW_LEVEL_QUOTE in message:
        # Yup, there was a quote.  Release the dequoter, man!
        message = _low_level_regexp.sub(_low_level_replace, message)

    if _CTCP_DELIMITER not in message:
        return [message]
    else:
        # Split it into parts.  (Does any IRC client actually *use*
        # CTCP stacking like this?)
        chunks = message.split(_CTCP_DELIMITER)

        messages = []
        i = 0
        while i < len(chunks) - 1:
            # Add message if it's non-empty.
            if len(chunks[i]) > 0:
                messages.append(chunks[i])

            if i < len(chunks) - 2:
                # Aye!  CTCP tagged data ahead!
                messages.append(tuple(chunks[i + 1].split(" ", 1)))

            i = i + 2

        if len(chunks) % 2 == 0:
            # Hey, a lonely _CTCP_DELIMITER at the end!  This means
            # that the last chunk, including the delimiter, is a
            # normal message!  (This is according to the CTCP
            # specification.)
            messages.append(_CTCP_DELIMITER + chunks[-1])

        return messages

def is_channel(string):
    """Check if a string is a channel name.

    Returns true if the argument is a channel name, otherwise false.
    """
    return string and string[0] in "#&+!"

def ip_numstr_to_quad(num):
    """
    Convert an IP number as an integer given in ASCII
    representation to an IP address string.

    >>> ip_numstr_to_quad('3232235521')
    '192.168.0.1'
    >>> ip_numstr_to_quad(3232235521)
    '192.168.0.1'
    """
    n = long(num)
    packed = struct.pack('>L', n)
    bytes = struct.unpack('BBBB', packed)
    return ".".join(map(str, bytes))

def ip_quad_to_numstr(quad):
    """
    Convert an IP address string (e.g. '192.168.0.1') to an IP
    number as a base-10 integer given in ASCII representation.

    >>> ip_quad_to_numstr('192.168.0.1')
    '3232235521'
    """
    bytes = map(long, quad.split("."))
    packed = struct.pack('BBBB', *bytes)
    return str(struct.unpack('>L', packed)[0])

class NickMask(str):
    """
    A nickmask (the source of an Event)
    """
    @property
    def nick(self):
        return self.split("!")[0]

    @property
    def userhost(self):
        return self.split("!")[1]

    @property
    def host(self):
        return self.split("@")[1]

    @property
    def user(self):
        return self.userhost.split("@"[0])

# for backward compatibility
def nm_to_n(s): return NickMask(s).nick
def nm_to_uh(s): return NickMask(s).userhost
def nm_to_h(s): return NickMask(s).host
def nm_to_u(s): return NickMask(s).user

def _ping_ponger(connection, event):
    "A global handler for the 'ping' event"
    connection.pong(event.target())

# for backward compatibility
parse_nick_modes = modes.parse_nick_modes
parse_channel_modes = modes.parse_channel_modes

generated_events = events.generated
protocol_events = events.protocol
numeric_events = events.numeric
all_events = events.all

irc_lower = strings.lower
