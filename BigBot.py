import ircbot
import irclib
import pdb
import urllib2
import json
import random
from string import Template

class BigBot(ircbot.SingleServerIRCBot):
	def __init__(self):
		ircbot.SingleServerIRCBot.__init__(self, [('jordanviard.com', 6667)], 'BigBot', 'Bot posteur de GIFs')
		self.sentences= [ "Petit coquin, va : $url",
				"Un gif plein d'amour pour toi, $name : $url",
				"Cadeau : $url",
				"Non tu n'en aura pas $name!" ]
	
	def on_welcome(self, serv, ev):
		print 'Joining #Yoshteam'
		serv.join('#YoshTeam')
	
	def getMsg(self, **arg):
		return Template(random.choice(self.sentences)).safe_substitute(arg)

	def getGif(self, message):
		tags = ""

		if message[0:4].lower()  == '!gif':
			contents = message.split(" ")
			cats = contents[1:]
			tags = '+'.join(cats)

		json_resp = urllib2.urlopen('http://api.giphy.com/v1/gifs/random?api_key=dc6zaTOxFJmzC&tag=' + str(tags)).read()
                rep = json.loads(json_resp)
		if len(rep[unicode('data')]) is 0:
			rep[unicode('data')] = {}
			rep[unicode('data')][unicode('image_url')] = "i__i"

		return rep

	def on_pubmsg(self, serv, ev):
		author = irclib.nm_to_n(ev.source())
		channel = ev.target()
		message = ev.arguments()[0].lower()
		
		if message.find('gif') is not -1:
			rep = self.getGif(message)
			serv.privmsg(channel,self.getMsg(name=author, url=rep[unicode('data')][unicode('image_url')]))

if __name__ == '__main__':
	print 'Bot running...'
	B = BigBot().start()
	print('>')
	command = input()
	print 'Commande recue : ' + command
	BigBot.die()
