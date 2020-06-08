#!/usr/bin/env python

import os
import time
import string
import threading

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GObject

pages = [
    'intro.html',
    'information.html',
    'desktop.html',
    'systemtray.html',
    'window.html',
    'notification.html',
    'browser.html',
    'basicapps.html',
    'security.html',
    'finish.html'
]


# Used as a decorator to run things in the background
def async(func):
    def wrapper(*args, **kwargs):
        thread = threading.Thread(target=func, args=args, kwargs=kwargs)
        thread.daemon = True
        thread.start()
        return thread
    return wrapper

# Used as a decorator to run things in the main loop, from another thread
def idle(func):
    def wrapper(*args):
        GObject.idle_add(func, *args)
    return wrapper



class Slideshow():
    def __init__(self, webviewObject, slideshowDirectory, language, intervalSeconds=30):
        self.browser = webviewObject
        self.loop = True
        self.interval = intervalSeconds
        #self.language = language   #Multi-lingual Support
        self.language = 'ko'    #Splash only supports Korean.
        self.slideshowDir = slideshowDirectory
        self.languageDir = self.getLanguageDirectory()
        self.template = os.path.join(slideshowDirectory, self.languageDir + 'intro.html')
        self.templateText = ''
        self.pageContent = []
        self.runLoop = True
        self.lastIndex = None

        try:
            # Prepare pages
            if os.path.isfile(self.template):
                # Preload all pages in an array
                for page in pages:
                   # Open content file
                    pagePath = os.path.join(self.languageDir, page)
                    if os.path.isfile(pagePath):
                        self.pageContent.append([pagePath])
                    else:
                        print 'Content path does not exist: ' + pagePath
            else:
                print 'Template path not found: ' + self.template
        except Exception, detail:
            print detail

    @async
    def run(self):
        # Update widget in main thread
        try:
            if self.pageContent:
                # Loop through all pages
                self.lastIndex = len(self.pageContent) - 1
                i = 1 #'information.html'
                
                while self.runLoop:
                    # Get the full path of the content page
                    if os.path.isfile(self.pageContent[i][0]):
                        self.updatePage(self.pageContent[i][0])

                        # Wait interval
                        time.sleep(self.interval)

                        # Reset counter when you need to loop the pages
                        if i == self.lastIndex-1:
                            i = 1 #'information.html'
                        else:
                            i = i + 1
                    else:
                        # You can only get here if you delete a file while in the loop
                        print 'Page not found: ' + self.pageContent[i][0]
            else:
                print 'No pages found to load'
        except Exception, detail:
            print detail

    def stop(self):
        self.runLoop = False
        self.updatePage(self.pageContent[self.lastIndex][0])

    @idle
    def updatePage(self, page):
        self.browser.load_uri('file:///'+page)

    def getLanguageDirectory(self):
        langDir = self.slideshowDir
        if self.language != '':
            testDir = os.path.join(self.slideshowDir, self.language + '/')
            if os.path.exists(testDir):
                langDir = testDir
            else:
                if "_" in self.language:
                    split = self.language.split("_")
                    if len(split) == 2:
                        testDir = os.path.join(self.slideshowDir, split[0]+'/')
                        if os.path.exists(testDir):
                            langDir = testDir
                else:   #default dir: ko
                    langDir = os.path.join(self.slideshowDir, 'ko/')

        return langDir
