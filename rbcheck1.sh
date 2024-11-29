#!/bin/bash

prj=openSUSE:Factory:Staging:adi:9
rbbaseprj=home:rb-checker


function cleanup
{
    local prj=$1
    osc rdelete -m drop -r "$prj"
}


function setuplink
{
  cat > .tmp <<EOF
<project name="$newprj">
  <title/>
  <description/>
  <url>/staging_workflows/openSUSE:Factory/staging_projects/$prj</url>
  <person userid="bmwiedemann" role="maintainer"/>
  <person userid="rb-checker" role="maintainer"/>
  <!--group groupid="factory-staging" role="maintainer"/-->
  <publish>
    <disable/>
  </publish>
  <debuginfo>
    <enable/>
  </debuginfo>
  <repository name="rb_j1" linkedbuild="all">
    <path project="openSUSE:Factory:Staging" repository="standard"/>
    <path project="openSUSE:Factory" repository="standard"/>
    <arch>x86_64</arch>
  </repository>
  <repository name="rb_future1y" linkedbuild="all">
    <path project="openSUSE:Factory:Staging" repository="standard"/>
    <path project="openSUSE:Factory" repository="standard"/>
    <arch>x86_64</arch>
  </repository>
</project>
EOF
  echo "osc meta prj -F .tmp $newprj"
  cat > .tmp <<EOF
%if "%_repository" == "rb_future1y"
Required: reproducible-faketools-futurepost
%endif

%if "%_repository" == "rb_j1"
Required: reproducible-faketools-j1
BuildFlags: nochecks
%endif

BuildFlags: nodisturl
Release: 1.1

Macros:
%distribution reproducible
EOF
  osc meta prjconf -F .tmp $newprj
  echo osc linkpac -r "$rev" "$srcprj" "$srcpkg" "$newprj"
}


function checkbranch
{
  osc r --format='%(status)s' "$newprj" "$srcpkg" > .tmp
  grep -q -e scheduled -e blocked -e signing -e dispatching .tmp && return # skip to wait some more
  if grep -v succeeded .tmp ; then
    echo "found unhandled status in $newprj => FIXME $BASH_SOURCE"
    return
  fi
  echo "succeeded, report status"
  # check status
  for repo in rb_future1y rb_j1 ; do
    osc api "/build/$newprj/$repo/x86_64/_repository?view=binaryversions" > .tmp.$repo
    grep -o 'hdrmd5="[0-9a-f]*"' .tmp.$repo | md5sum
  done | sort | uniq -c | perl -ne 'm/^ *[013-9] / && exit 1'
  reproducible=$?
 
  # FIXME report status
  echo $reproducible > .tmp
  cat .tmp.rb_future1y .tmp.rb_j1 >>.tmp
  osc api -X PUT --file .tmp "/source/$report"
  #TODO email Bernhard
  #TODO comment on SR
  echo TODO cleanup "$newprj"
}


pkgs=$(osc ls $prj)
for pkg in $pkgs ; do
  osc cat -u "$prj" "$pkg" _link > .tmp
  rev=$(perl -ne 'm/rev="([a-f0-9]+)"/ && print $1' .tmp)
  srcprj=$(perl -ne 'm/project="([^"]+)"/ && print $1' .tmp)
  srcpkg=$(perl -ne 'm/package="([^"]+)"/ && print $1' .tmp)
  newprj=$rbbaseprj:rebuild:$srcpkg-$rev
  report=$rbbaseprj/reports/$srcpkg-$rev
  branchexists=$(if osc ls $newprj >/dev/null 2>&1 ; then echo true ; else echo false ; fi )
  reportexists=$(if osc ls $report >/dev/null 2>&1 ; then echo true ; else echo false ; fi )
  if $reportexists ; then # we are done ; move on to next pkg
      continue
  fi
  if ! $branchexists ; then
      setuplink
  else
      checkbranch
  fi
done
