from string import Template
# import urllib2
# import json
# import random
from string import Template
# import re
# import requests
# from pyquery import PyQuery as pq
import praw

class Reddit:
	name="Reddit"
	r = praw.Reddit(user_agent="IRCBot/1.0 by yoshTeam")

	@classmethod
	def get_urls(cls,generator, args):
		urls = []
		for thing in generator:
			if thing.url not in urls:
				urls.append(thing.url)
		return urls

	@classmethod
	def gif(cls,args=[]):
		if(args==[]):
			sub='gifs'
		else:
			sub=args[0];

		subb = cls.r.get_subreddit(sub)
		try:
			url=cls.r.get_random_submission(sub).url
		except Exception as e:
			return False, "Bad subreddit name ("+ sub+") or it's nsfw."
		return True,url

		# tabs= ['','new/','rising/','controversial/','top/','gilded/']
		# url = u"http://fr.reddit.com/r/"+sub+"/"+random.choice(tabs)
		# print(url)
		# user_agent = {'User-agent': 'IRCBot/1.0 by yoshTeam'}
		# req = requests.get(url,headers = user_agent)
		# if(not req.ok):
		# 	return False,"Bad subbreddit name ("+sub+") or too many requests."
		# document = pq(req.text).make_links_absolute(url)
		# title=document('title').html()
		# print(title)
		# if len(document('div.thing div.entry a.title')) is 0:
		# 	return False,"Nothing found in the page (or you enter a nsfw subreddit)!"
		# return True,random.choice(document('div.thing div.entry a.title')).get('href')

	@classmethod
	def msg(cls,template,args=[]):
		print("Arguement :",args)
		worked,gif_url= cls.gif(args)
		if worked:
			return Template(template).safe_substitute({'url' : gif_url})
		else:
			return gif_url