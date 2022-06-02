#! /usr/bin/python3

import bugzilla
import osc.core
import osc.conf
from lxml import etree as ET
import re
import os
import yaml
import argparse
import sys

from osclib.core import entity_email


def update_prjconfig(apiurl, project, target):
    url = osc.core.makeurl(apiurl, ["build", project, 'images', 'local', '000product:SLES-cd-Full-x86_64'])
    root = ET.parse(osc.core.http_GET(url))
    for binary in root.findall('binary'):
        if re.match(r'SLE-.*-Full-x86_64-Build.*-Media1\.report', binary.get('filename')):
            filename = binary.get('filename')
            break

    layers = {'sp3': set(), 'sp2': set(), 'sp1': set(), 'sp0': set()}
    binaryexamples = dict()
    url = osc.core.makeurl(apiurl, ["build", project, 'images', 'local', '000product:SLES-cd-Full-x86_64', filename])
    root = ET.parse(osc.core.http_GET(url))
    packages = set()
    for binary in root.findall('binary'):
        package = binary.get('package').split(':')[0]
        m = re.match(r'(.*)\.(\d+)$', package)
        if m:
            possible_incident_nr = m.group(2)
            if re.search(f'SUSE:Maintenance:{possible_incident_nr}', binary.get('disturl')):
                package = m.group(1)
        binaryexamples.setdefault(package, set())
        binaryexamples[package].add(binary.get('name'))
        for layer in ['SP1', 'SP2', 'SP3']:
            if binary.text.startswith(f'obs://SUSE:SLE-15-{layer}:'):
                layers[layer.lower()].add(package)
        if binary.text.startswith('obs://SUSE:SLE-15:'):
            layers['sp0'].add(package)
        packages.add(package)

    config = ''
    for package in sorted(packages):
        config += "BuildFlags: onlybuild:" + package + "\n"

    url = osc.core.makeurl(apiurl, ["source", target, '_config'])
    osc.core.http_PUT(url, data=config)

    for layer in layers:
        config = ''
        for package in sorted(layers[layer]):
            config += "BuildFlags: onlybuild:" + package + "\n"

        url = osc.core.makeurl(apiurl, ["source", target + f'-layer-{layer}', '_config'])
        osc.core.http_PUT(url, data=config)

    return binaryexamples


def bugzilla_init(apiurl):
    bugzilla_api = bugzilla.Bugzilla(apiurl)
    if not bugzilla_api.logged_in:
        print('Bugzilla credentials required to create bugs.')
        bugzilla_api.interactive_login()
    return bugzilla_api


def bug_create(bugzilla_api, meta, assigned_to, cc, summary, description):
    createinfo = bugzilla_api.build_createbug(
        product=meta[0],
        component=meta[1],
        version=meta[2],
        severity='normal',
        op_sys='Linux',
        platform='PC',
        assigned_to=assigned_to,
        cc=cc,
        summary=summary,
        description=description)
    newbug = bugzilla_api.createbug(createinfo)

    return newbug.id


def bug_owner(apiurl, package, entity='person'):
    query = {
        'package': package,
    }
    url = osc.core.makeurl(apiurl, ('search', 'owner'), query=query)
    root = ET.parse(osc.core.http_GET(url)).getroot()

    bugowner = root.find('.//{}[@role="bugowner"]'.format(entity))
    if bugowner is not None:
        return entity_email(apiurl, bugowner.get('name'), entity)
    maintainer = root.find('.//{}[@role="maintainer"]'.format(entity))
    if maintainer is not None:
        return entity_email(apiurl, maintainer.get('name'), entity)
    if entity == 'person':
        return bug_owner(apiurl, package, 'group')

    return None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Send e-mails about packages failing to build for a long time')
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument("--max-bugs", metavar="MAXBUGS", help="Create max bugs", default=0)

    args = parser.parse_args()
    bugzilla_api = bugzilla_init('https://bugzilla.suse.com')

    apiurl = "https://api.suse.de"

    osc.conf.get_config(override_apiurl=apiurl)

    project = "SUSE:SLE-15-SP4:GA"
    target = "home:coolo:rebuild-sp4"
    binaryexamples = update_prjconfig(apiurl, project, target)

    with open('rebuildsps-reported-bugs.yaml') as f:
        created_bugs = yaml.safe_load(f)
    url = osc.core.makeurl(apiurl, ["build", target, '_result'])
    # osc's makeurl doesn't seem to be able to repeat keys
    url += "?code=failed&code=unresolvable"
    root = ET.parse(osc.core.http_GET(url))
    counter = 0
    for s in root.findall('.//status'):
        package = s.get('package')
        examples = ', '.join(binaryexamples.get(package))

        id = created_bugs.get(package)
        if id:
            print(f'known bug for {package}: {id} - {examples}')
            continue

        owner = bug_owner(apiurl, package)
        if not owner:
            print(f"No bug owner found for {package}")
            owner = 'coolo@suse.com'
        # special case for whatever reason
        if owner == 'lrupp@suse.com':
            owner = 'lars.vogdt@suse.com'
        if owner == 'openssl-maintainers@suse.de':
            owner = 'pmonrealgonzalez@suse.com'
        if counter >= int(args.max_bugs):
            print(f"Would file a bug for {owner} about {package}:{examples}")
            continue
        text = "I test compiled all sources appearing on SP4 Full ISO within IBS. And %(package)s failed to build there.\n\nPlease see https://build.suse.de/package/show/home:coolo:rebuild-sp4/%(package)s\n\n" % {
            'package': package}
        text += f"FYI: The binary packages found on the SP4 medium are: {examples}"
        id = bug_create(bugzilla_api, ['SUSE Linux Enterprise Server 15 SP4', 'Maintenance',
                        'unspecified'], owner, '', f"FTBFS: {package} won't compile on SP4", text)
        print(package, id)
        created_bugs[package] = id
        with open('rebuildsps-reported-bugs.yaml.new', 'w') as f:
            f.write(yaml.dump(created_bugs))
        os.rename('rebuildsps-reported-bugs.yaml.new', 'rebuildsps-reported-bugs.yaml')
        counter += 1
