#!/usr/bin/env python3

import os
import time
import string
import threading

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GObject

#pages = [
#'welcome.html',
#'giants.html',
#'desktop-ready.html',
#'windows.html',
#'safe.html',
#'community.html'
#]
pages = [
'welcome.html'
]


# Used as a decorator to run things in the background
async def func():
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
    def __init__(self, webviewObject, slideshowDirectory, language='', intervalSeconds=30, loopPages=False):
        self.browser = webviewObject
        self.loop = loopPages
        self.interval = intervalSeconds
        self.language = language
        self.slideshowDir = slideshowDirectory
        self.languageDir = self.getLanguageDirectory()
        self.template = os.path.join(slideshowDirectory, 'template.html')
        self.templateText = ''
        self.pageContent = []

        try:
            # Prepare pages
            if os.path.isfile(self.template):
                print('Template path: ' + self.template)
                tmplFile = open(self.template,'r')
                self.templateText = tmplFile.read()
                tmplFile.close()

                # Preload all pages in an array
                chkString = '<div id="container">'
                if chkString in self.templateText:
                    for page in pages:
                        # Open content file
                        pagePath = os.path.join(self.languageDir, page)
                        if os.path.isfile(pagePath):
                            contFile = open(pagePath, 'r')
                            # Merge content with template
                            html = self.templateText.replace(chkString, chkString + contFile.read())
                            self.pageContent.append([pagePath, html])
                            contFile.close()
                        else:
                            print('Content path does not exist: ' + pagePath)
                else:
                    print('Check string not found in template: ' + chkString)
            else:
                print('Template path not found: ' + self.template)
        except Exception as detail:
            print(detail)

    def run(self):
        # Update widget in main thread
        try:
            if self.pageContent:
                # Loop through all pages
                lastIndex = len(self.pageContent) - 1
                runLoop = True
                i = 0
                while runLoop:
                    # Get the full path of the content page
                    if os.path.isfile(self.pageContent[i][0]):
                        self.updatePage(self.pageContent[i][1])

                        # Wait interval
                        time.sleep(self.interval)

                        # Reset counter when you need to loop the pages
                        if i == lastIndex:
                            if self.loop:
                                i = 0
                            else:
                                runLoop = False
                        else:
                            i = i + 1
                    else:
                        # You can only get here if you delete a file while in the loop
                        print('Page not found: ' + self.pageContent[i][0])
            else:
                print('No pages found to load')
        except Exception as detail:
            print(detail)

    @idle
    def updatePage(self, page):
        self.browser.load_html(page, 'file:///')

    def getLanguageDirectory(self):
        langDir = self.slideshowDir
        if self.language != '':
            testDir = os.path.join(self.slideshowDir, 'loc.' + self.language)
            if os.path.exists(testDir):
                langDir = testDir
            else:
                if "_" in self.language:
                    split = self.language.split("_")
                    if len(split) == 2:
                        testDir = os.path.join(self.slideshowDir, 'loc.' + split[0])
                        if os.path.exists(testDir):
                            langDir = testDir
        return langDir
