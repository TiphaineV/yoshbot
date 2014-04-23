from string import Template
import urllib2
import json

from string import Template
import re
import requests
from pyquery import PyQuery as pq

class Giphy:
	name="Giphy"

	@classmethod
	def gif(cls,args=[]):
		strTag='+'.join(args)
		json_resp = urllib2.urlopen('http://api.giphy.com/v1/gifs/random?api_key=dc6zaTOxFJmzC&tag=' + str(strTag)).read()
        	rep = json.loads(json_resp)
		if len(rep[unicode('data')]) is 0:
			return ''

		return rep[unicode('data')][unicode('image_url')]

	@classmethod
	def msg(cls,template,args=[]):
		gif_url= cls.gif(args)
		if gif_url=='':
			return "failed to get a gif because "+'+'.join(args)+ " was not found."
		return Template(template).safe_substitute({'url' : gif_url})