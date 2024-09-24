#!/usr/bin/python3

import argparse
import logging
import os
import re
import sys
from collections import namedtuple
from urllib.error import HTTPError

import osc.core
import yaml
from lxml import etree as ET

from osclib.comments import CommentAPI
from osclib.conf import Config
from osclib.conf import str2bool
from osclib.core import (builddepinfo, depends_on, duplicated_binaries_in_repo,
                         fileinfo_ext_all, repository_arch_state,
                         repository_path_expand, target_archs)

from osclib.repochecks import rbcheck, mirror
from osclib.stagingapi import StagingAPI
from osclib.memoize import memoize

SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
CheckResult = namedtuple('CheckResult', ('success', 'comment'))

repositories = list("rb_future1y", "rb_j1")


class RBChecker(object):
    def __init__(self, api, config):
        self.api = api
        self.logger = logging.getLogger('RBChecker')
        self.commentapi = CommentAPI(api.apiurl)

        self.arch_whitelist = config.get('rb_checker-arch-whitelist')
        if self.arch_whitelist:
            self.arch_whitelist = set(self.arch_whitelist.split(' '))

        self.ring_whitelist = set(config.get('rb_checker-binary-whitelist-ring', '').split(' '))

        self.ignore_unreproducible = set(config.get('rbcheck-ignore-unreproducible-binaries', '').split(' '))
        self.ignore_conflicts = set(config.get('rbcheck-ignore-conflicts', '').split(' ')) # TODO: drop

    @memoize(session=True)
    def pkg_with_multibuild_flavors(self, package):
        ret = set([package])
        # Add all multibuild flavors
        mainprjresult = ET.fromstringlist(osc.core.show_results_meta(self.api.apiurl, self.api.project, multibuild=True))
        for pkg in mainprjresult.xpath(f"result/status[starts-with(@package,'{package}:')]"):
            ret.add(pkg.get('package'))

        return ret

    def packages_to_ignore(self, project):
        comments = self.commentapi.get_comments(project_name=project)
        ignore_re = re.compile(r'^rbcheck: ignore (?P<args>.*)$', re.MULTILINE)

        # the last wins, for now we don't care who said it
        args = []
        for comment in comments.values():
            match = ignore_re.search(comment['comment'].replace('\r', ''))
            if not match:
                continue
            args = match.group('args').strip()
            # allow space and comma to seperate
            args = args.replace(',', ' ').split(' ')
        return set(args)

    def staging(self, project, force=False):
        """project is e.g. openSUSE:Factory:Staging:adi:65:reproducible
        force means to re-check a previously checked project
        """
        api = self.api

        repository = self.api.cmain_repo

        # fetch the build ids at the beginning - mirroring takes a while
        buildids = {}
        try:
            architectures = self.target_archs(project, repository)
        except HTTPError as e:
            if e.code == 404:
                # adi disappear all the time, so don't worry
                return False
            raise e

        all_done = True
        for arch in architectures:
            pra = f'{project}/{repository}/{arch}'
            buildid = self.buildid(project, repository, arch)
            if not buildid:
                self.logger.error(f'No build ID in {pra}')
                return False
            buildids[arch] = buildid
            url = self.report_url(project, repository, arch, buildid)
            try:
                root = ET.parse(osc.core.http_GET(url)).getroot()
                check = root.find('check[@name="rbcheck"]/state')
                if check is not None and check.text != 'pending':
                    self.logger.info(f'{pra} already "{check.text}", ignoring')
                else:
                    all_done = False
            except HTTPError:
                self.logger.info(f'{pra} has no status report')
                all_done = False

        if all_done and not force:
            return True

        repository_pairs = repository_path_expand(api.apiurl, project, repository)
        result_comment = []

        result = True
        to_ignore = self.packages_to_ignore(project)
        status = api.project_status(project)
        if status is None:
            self.logger.error(f'no project status for {project}')
            return False

        for arch in architectures:
            # hit the first repository in the target project (if existant)
            target_pair = None
            directories = []
            for pair_project, pair_repository in repository_pairs:
                # ignore repositories only inherited for config
                if repository_arch_state(self.api.apiurl, pair_project, pair_repository, arch):
                    if not target_pair and pair_project == api.project:
                        target_pair = [pair_project, pair_repository]

                    directories.append(mirror(self.api.apiurl, pair_project, pair_repository, arch))

            if not api.is_adi_project(project):
                # For "leaky" ring packages in letter stagings, where the
                # repository setup does not include the target project, that are
                # not intended to to have all run-time dependencies satisfied.
                whitelist = self.ring_whitelist
            else:
                whitelist = set()

            whitelist |= to_ignore
            ignore_conflicts = self.ignore_conflicts | to_ignore

            check = self.cycle_check(project, repository, arch)
            if not check.success:
                self.logger.warning('Cycle check failed')
                result_comment.append(check.comment)
                result = False

            check = self.install_check(directories, arch, whitelist, ignore_conflicts)
            if not check.success:
                self.logger.warning('Install check failed')
                result_comment.append(check.comment)
                result = False

        duplicates = duplicated_binaries_in_repo(self.api.apiurl, project, repository)
        # remove white listed duplicates
        for arch in list(duplicates):
            for binary in self.ignore_duplicated:
                duplicates[arch].pop(binary, None)
            if not len(duplicates[arch]):
                del duplicates[arch]
        if len(duplicates):
            self.logger.warning('Found duplicated binaries')
            result_comment.append('Found duplicated binaries')
            result_comment.append(yaml.dump(duplicates, default_flow_style=False))
            result = False

        if result:
            self.report_state('success', self.gocd_url(), project, repository, buildids)
        else:
            result_comment.insert(0, f'Generated from {self.gocd_url()}\n')
            self.report_state('failure', self.upload_failure(project, result_comment), project, repository, buildids)
            self.logger.warning(f'Not accepting {project}')
            return False

        return result

    def upload_failure(self, project, comment):
        print(project, '\n'.join(comment))
        url = self.api.makeurl(['source', 'home:rb-checker', 'reports', project])
        osc.core.http_PUT(url, data='\n'.join(comment))

        url = self.api.apiurl.replace('api.', 'build.')
        return f'{url}/package/view_file/home:rb-checker/reports/{project}'

    def report_state(self, state, report_url, project, repository, buildids):
        architectures = self.target_archs(project, repository)
        for arch in architectures:
            self.report_pipeline(state, report_url, project, repository, arch, buildids[arch])

    def gocd_url(self):
        if not os.environ.get('GO_SERVER_URL'):
            # placeholder :)
            return 'http://bernhard.bmwiedemann.de/'
        report_url = os.environ.get('GO_SERVER_URL').replace(':8154', '')
        return report_url + '/tab/build/detail/{}/{}/{}/{}/{}#tab-console'.format(os.environ.get('GO_PIPELINE_NAME'),
                                                                                  os.environ.get('GO_PIPELINE_COUNTER'),
                                                                                  os.environ.get('GO_STAGE_NAME'),
                                                                                  os.environ.get('GO_STAGE_COUNTER'),
                                                                                  os.environ.get('GO_JOB_NAME'))

    def buildid(self, project, repository, architecture):
        url = self.api.makeurl(['build', project, repository, architecture], {'view': 'status'})
        root = ET.parse(osc.core.http_GET(url)).getroot()
        buildid = root.find('buildid')
        if buildid is None:
            return False
        return buildid.text

    def report_url(self, project, repository, architecture, buildid):
        return self.api.makeurl(['status_reports', 'built', project,
                                 repository, architecture, 'reports', buildid])

    def report_pipeline(self, state, report_url, project, repository, architecture, buildid):
        url = self.report_url(project, repository, architecture, buildid)
        name = 'rbcheck'
        xml = self.check_xml(report_url, state, name)
        try:
            osc.core.http_POST(url, data=xml)
        except HTTPError:
            print('failed to post status to ' + url)
            sys.exit(1)

    def check_xml(self, url, state, name):
        check = ET.Element('check')
        if url:
            se = ET.SubElement(check, 'url')
            se.text = url
        se = ET.SubElement(check, 'state')
        se.text = state
        se = ET.SubElement(check, 'name')
        se.text = name
        return ET.tostring(check)

    def target_archs(self, project, repository):
        archs = target_archs(self.api.apiurl, project, repository)

        # Check for arch whitelist and use intersection.
        if self.arch_whitelist:
            archs = list(self.arch_whitelist.intersection(set(archs)))

        # Trick to prioritize x86_64.
        return sorted(archs, reverse=True)

    def install_check(self, directories, arch, whitelist, ignored_conflicts):
        self.logger.info(f"install check: start (whitelist:{','.join(whitelist)})")
        parts = rbcheck(directories, arch, whitelist, ignored_conflicts)
        if len(parts):
            header = f'### [install check & file conflicts for {arch}]'
            return CheckResult(False, header + '\n\n' + ('\n' + ('-' * 80) + '\n\n').join(parts))

        self.logger.info('install check: passed')
        return CheckResult(True, None)

    def cycle_check(self, project, repository, arch):
        self.logger.info(f'cycle check: start {project}/{repository}/{arch}')
        comment = []

        depinfo = builddepinfo(self.api.apiurl, project, repository, arch, order=False)
        for cycle in depinfo.findall('cycle'):
            for package in cycle.findall('package'):
                package = package.text
                allowed = False
                for acycle in self.allowed_cycles:
                    if package in acycle:
                        allowed = True
                        break
                if not allowed:
                    cycled = [p.text for p in cycle.findall('package')]
                    comment.append(f"Package {package} appears in cycle {'/'.join(cycled)}")

        if len(comment):
            # New cycles, post comment.
            self.logger.info('cycle check: failed')
            return CheckResult(False, '\n'.join(comment) + '\n')

        self.logger.info('cycle check: passed')
        return CheckResult(True, None)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Do an RB-Check for reproducible builds on staging project')
    parser.add_argument('-s', '--staging', type=str, default=None,
                        help='staging project')
    parser.add_argument('-p', '--project', type=str, default='openSUSE:Factory',
                        help='project to check (ex. openSUSE:Factory, openSUSE:Leap:15.1)')
    parser.add_argument('-d', '--debug', action='store_true', default=False,
                        help='enable debug information')
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')

    args = parser.parse_args()

    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    apiurl = osc.conf.config['apiurl']
    config = Config.get(apiurl, args.project)
    api = StagingAPI(apiurl, args.project)
    staging_report = RBChecker(api, config)

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if args.staging:
        if not staging_report.staging(api.prj_from_short(args.staging), force=True):
            sys.exit(1)
    else:
        for staging in api.get_staging_projects():
            if api.is_adi_project(staging):
                staging_report.staging(staging)
    sys.exit(0)
