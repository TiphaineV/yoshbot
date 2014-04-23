import irc.client
import irc.bot
import pdb
import urllib2
import json
import random
from string import Template
import re
import requests
from pyquery import PyQuery as pq
from giphy import *




class BigBot(irc.bot.SingleServerIRCBot):
	def __init__(self):
		irc.bot.SingleServerIRCBot.__init__(self, [('jordanviard.com', 6667)], 'Bigbot', 'Bot posteur de GIFs')
		self.sources = ["giphy","twitter",'reddit']
		self.sentences = [ "Petit coquin, va : $url",
				"Un gif plein d'amour pour toi, $name : $url",
				"Cadeau : $url",
				"Non tu n'en aura pas $name!" ]
	
	def on_welcome(self, serv, ev):
		print 'Joining #Yoshteam'
		serv.join('#YoshTeam')
	
	def getMsg(self, **arg):
		return Template(random.choice(self.sentences)).safe_substitute(arg)

	def giphy(self,tags):
		tab = tags.split(" ")
		strTag='+'.join(tab)
		json_resp = urllib2.urlopen('http://api.giphy.com/v1/gifs/random?api_key=dc6zaTOxFJmzC&tag=' + str(strTag)).read()
        	rep = json.loads(json_resp)
		if len(rep[unicode('data')]) is 0:
			rep[unicode('data')] = {}
			rep[unicode('data')][unicode('image_url')] = "i__i"

		return rep[unicode('data')][unicode('image_url')] 

	def reddit(self,subreddit):
		if(subreddit==""):
			sub='gifs'
		else:
			sub=subreddit.split(" ")[1];
		tabs= ['','new/','rising/','controversial/','top/','gilded/']
		url = u"http://fr.reddit.com/r/"+sub+"/"+random.choice(tabs)
		print(url)
		req = requests.get(url)
		if(not req.ok):
			return "Bad subbreddit name or too many requests."
		document = pq(req.text).make_links_absolute(url)
		title=document('title').html()
		print(title)
		if len(document('div.thing div.entry a.title')) is 0:
			return "No div.entry there !"

		return random.choice(document('div.thing div.entry a.title')).get('href')



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
		if(source == "giphy"):
			print('giphy')
			return self.giphy(tags)
		elif(source == "twitter"):
			print("twitter")
			return self.giphy(tags)
		elif source == "reddit":
			print('reddit')
			return self.reddit(tags)
		
		print("non reconnue")

	def on_pubmsg(self, serv, ev):
		author = irc.client.nm_to_n(ev.source())
		channel = ev.target()
		message = ev.arguments()[0].lower()
		
		if any(message.find(i) is not -1 for i in self.sources):
			rep = self.getGif(message)
			serv.privmsg(channel,self.getMsg(name=author, url=rep))
	
		if message.find("bonjour bigbot") is not -1:
			serv.privmsg(channel, "Bonjour " + str(author) + "! C'est gentil de me parler :)" )

if __name__ == '__main__':
	print 'Bot running...'
	B = BigBot().start()
	print('>')
	command = input()
	print 'Commande recue : ' + command
	BigBot.die()
