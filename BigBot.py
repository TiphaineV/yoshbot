import ircbot
import irclib
import pdb
import urllib2
import json
import random
from string import Template
import re

class BigBot(ircbot.SingleServerIRCBot):
	def __init__(self):
		ircbot.SingleServerIRCBot.__init__(self, [('jordanviard.com', 6667)], 'BigBot', 'Bot posteur de GIFs')
		self.sources = ["giphy","twitter"]
		self.sentences = [ "Petit coquin, va : $url",
				"Un gif plein d'amour pour toi, $name : $url",
				"Cadeau : $url",
				"Non tu n'en aura pas $name!" ]
	
	def on_welcome(self, serv, ev):
		print 'Joining #Yoshteam'
		serv.join('#YoshTeam')
	
	def getMsg(self, **arg):
		return Template(random.choice(self.sentences)).safe_substitute(arg)

	def getGiphy(self,tags):
		tab = tags.split(" ")
		strTag='+'.join(tab)
		json_resp = urllib2.urlopen('http://api.giphy.com/v1/gifs/random?api_key=dc6zaTOxFJmzC&tag=' + str(strTag)).read()
        	rep = json.loads(json_resp)
		if len(rep[unicode('data')]) is 0:
			rep[unicode('data')] = {}
			rep[unicode('data')][unicode('image_url')] = "i__i"

		return rep

	def getGif(self, message):
		tags = []
		m = re.search('(?<=\!)(\w+)(.*)', message)
		
		if (m is not None):
			if m.group(1) not in self.sources:
				source = random.choice(self.sources)
			else:
				source=m.group(1)
			tags=m.group(2)
		else:
			source=random.choice(self.sources)
			tags=''
		pdb.set_trace()
		if(source == "giphy"):
			print('giphy')
			return self.getGiphy(tags)
		elif(source == "twitter"):
			print("twitter")
			return self.getGiphy(tags)
		
		print("non reconnue")

	def on_pubmsg(self, serv, ev):
		author = irclib.nm_to_n(ev.source())
		channel = ev.target()
		message = ev.arguments()[0].lower()
		
		if message.find('gif') is not -1 or message.find('giphy') is not -1 or message.find('twitter') is not -1:
			rep = self.getGif(message)
			serv.privmsg(channel,self.getMsg(name=author, url=rep[unicode('data')][unicode('image_url')]))
		
if __name__ == '__main__':
	print 'Bot running...'
	B = BigBot().start()
	print('>')
	command = input()
	print 'Commande recue : ' + command
	BigBot.die()
