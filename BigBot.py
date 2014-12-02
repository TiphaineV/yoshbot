import irc.client
import irc.bot
import json
import random
from string import Template
import re
from giphy import *
from reddit import *



class BigBot(irc.bot.SingleServerIRCBot):
	def __init__(self):
		irc.bot.SingleServerIRCBot.__init__(self, [('irc.freenode.net', 6667)], 'BigBot', 'Bot posteur de GIFs')
		self.sources = {"giphy":Giphy(), "reddit":Reddit()}
		self.sentences = [ "Petit coquin, va : $url",
				"Un gif plein d'amour pour toi, $name : $url",
				"Cadeau : $url",
				"Non tu n'en aura pas $name!" ]
	
	def on_welcome(self, serv, ev):
		print 'Joining #bitesvolantes'
		serv.join('#bitesvolantes')
	
	def answer(self,query,context):
		tags = []
		m = re.search('(\w+)(.+)?', query)		
		if (m is not None):
			if m.group(1) not in self.sources:
				source = random.choice(list(self.sources.values()))
				tags=[]
			else:
				source=self.sources[m.group(1)]
				print(m.groups())
				if(m.group(2)):
					tags=m.group(2).lstrip(' ').split(" ")
				else:
					print("Rien du tout.")
					tags=[]				
		else:
			source=random.choice(list(self.sources.values()))
			tags=[]
		print("Tags",tags)
		answerTemplate=Template(random.choice(self.sentences)).safe_substitute(context) # replace everything like $authoo&co so that only $url remains.
		finalAns=source.msg(answerTemplate,tags)+' (Provided by '+source.name+')'

		print("Final ",finalAns)
		return finalAns

	def on_pubmsg(self, serv, ev):
		author = irc.client.nm_to_n(ev.source())
		channel = ev.target()
		message = ev.arguments()[0].lower()
		
		if any(message.find(i) is not -1 for i in self.sources):
			context={'name':author,'chan':channel}			
			rep=self.answer(message,context)
			serv.privmsg(channel,rep)
	
		if message.find("bonjour bigbot") is not -1:
			serv.privmsg(channel, "Bonjour " + str(author) + "! C'est gentil de me parler :)" )

if __name__ == '__main__':
	print 'Bot running...'
	B = BigBot().start()
	print('>')
	command = input()
	print 'Commande recue : ' + command
	BigBot.die()
