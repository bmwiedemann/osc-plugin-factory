#!/usr/bin/python3
# -*- coding: utf-8 -*-
# Copyright (c) 2018 SUSE LLC
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from __future__ import print_function

from lxml import etree as ET
from urllib.parse import urlparse

import sys
import re
import cmdln
import logging
import urllib
import osc.core

import ToolBase

logger = logging.getLogger()

class ReportTool(ToolBase.ToolBase):

    def __init__(self):
        ToolBase.ToolBase.__init__(self)
        
    def collect_products(self):
        url = self.makeurl(['build', self.reference_project, 'images', 'local'])
        root = ET.fromstring(self.cached_GET(url))
        product_re = re.compile('000product:.*x86_64')
        ret = []
        for node in root.findall('./entry'):
            name = node.get('name')
            if not product_re.match(name):
                continue
            # just the garbage bag
            if name.startswith('000product:sle-module-development-tools-obs'):
                continue
            ret.append(name)
        return ret

    def collect_reports(self):
        ret = []
        for product in self.collect_products():
            url = self.makeurl(['build', self.reference_project, 'images', 'local', product])
            root = ET.fromstring(self.cached_GET(url))
            for node in root.findall('./binary'):
                filename = node.get('filename')
                if not filename.endswith('-Media2.report'):
                    continue
                report_url = self.makeurl(['build', self.reference_project, 'images', 'local', product, filename])
                ret.append(report_url)
        return ret

    def collect(self):
        disturls = set()
        for url in self.collect_reports():
            root = ET.fromstring(self.cached_GET(url))
            for node in root.findall('./binary'):
                disturls.add(node.get('disturl'))
        config = set()
        for url in sorted(disturls):
            config.add(self.translate(url))
        for line in sorted(config):
            print(line)

    def translate(self, url):
        print(url)
        url = urlparse(url)
        paths = url.path.split('/')
        project = paths[1]
        basename = paths[-1]
        md5 = basename.split('-')[0]
        name = '-'.join(basename.split('-')[1:])
        # strip multibuild
        split_name = name.split(':')
        if len(split_name) > 1:
            name = split_name[0]
        # strip channel suffix
        if project.startswith('SUSE:Maintenance'):
            split_name = name.split('.')
            name = '.'.join(split_name[:-1])
        return f'BuildFlags: onlybuild:{name}'

class CommandLineInterface(ToolBase.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ToolBase.CommandLineInterface.__init__(self, args, kwargs)

    def get_optparser(self):
        parser = ToolBase.CommandLineInterface.get_optparser(self)
        parser.add_option('-p', '--project', dest='project', metavar='PROJECT',
                        help='project to process')
        parser.add_option('--reference-project', metavar='PROJECT',
                dest='reference_project', help='reference project')
        return parser

    def setup_tool(self):
        tool = ReportTool()
        tool.project = self.options.project
        tool.reference_project = self.options.reference_project
        return tool

    def do_collect(self, subcmd, opts):
        """${cmd_name}: collect media2 reports

        ${cmd_usage}
        ${cmd_option_list}
        """
        self.tool.collect()
        
        

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
