#!/bin/bash

prj=${1:-openSUSE:Factory:Staging:adi:9}
rbbaseprj=home:rb-checker
repos="rb_future1y rb_j1"


# test if an API endpoint returns success or an error
function test_api_exists
{
    curl -s --fail-with-body https://api.opensuse.org/$1 >/dev/null 2>&1
}


function cleanup
{
    local prj=$1
    echo osc rdelete -m drop -r "$prj" | tee -a .cleanup
}


function setuplink
{
  echo "setting up test for $newprj $pkg"
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
    <path project="$prj" repository="standard"/>
    <path project="home:bmwiedemann:reproducible" repository="openSUSE_Tumbleweed"/>
    <arch>x86_64</arch>
  </repository>
  <repository name="rb_future1y" linkedbuild="all">
    <path project="openSUSE:Factory:Staging" repository="standard"/>
    <path project="$prj" repository="standard"/>
    <path project="home:bmwiedemann:reproducible" repository="openSUSE_Tumbleweed"/>
    <arch>x86_64</arch>
  </repository>
</project>
EOF
  echo "$newprj" >> .cleanuplater
  $dry osc meta prj -F .tmp "$newprj" || exit 11
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
  $dry osc meta prjconf -F .tmp "$newprj" || exit 12
  $dry osc linkpac -r "$rev" "$srcprj" "$srcpkg" "$newprj" || exit 13
}


function checkbranch
{
  osc r --format='%(status)s' "$newprj" "$srcpkg" > .tmp
  grep -q -e finished -e scheduled -e blocked -e building -e signing -e dispatching .tmp && return # skip to wait some more
  if grep -v succeeded .tmp ; then
    echo "found unhandled status in $newprj => FIXME $BASH_SOURCE"
    return
  fi
  echo "succeeded, report status"
  # check status
  for repo in $repos ; do
    osc api "/build/$newprj/$repo/x86_64/_repository?view=binaryversions" > .tmp.$repo
    grep -o 'hdrmd5="[0-9a-f]*"' .tmp.$repo | md5sum
  done | sort | uniq -c | perl -ne 'm/^ *[013-9] / && exit 1'
  unreproducible=$?

  # FIXME report status
  echo -e "v1 $unreproducible\n$prj $pkg\n" https://build.opensuse.org/package/show/$newprj/$srcpkg > .tmp # 0=reproducible 1=unreproducible
  for repo in $repos ; do
      cat .tmp.$repo >>.tmp
      rm .tmp.$repo
  done
  osc api -X PUT --file .tmp "/source/$report"
  #TODO email Bernhard about unreproducible submissions
  if [[ $unreproducible = 1 ]] ; then
      echo "$pkg is unreproducible -> sending email"
      echo -e "unreproducible $prj $pkg\nhttps://build.opensuse.org/package/show/$newprj/$srcpkg" |
        mailx -a .tmp -s "unreproducible package $pkg" rbcheckerbmw@lsmod.de
  fi
  #TODO add comment/report/review on SR
  cleanup "$newprj"
}


pkgs=$(osc ls $prj | grep -v :)
for pkg in $pkgs ; do
  osc cat -u "$prj" "$pkg" _link > .tmp || continue
  rev=$(perl -ne 'm/rev="([a-f0-9]+)"/ && print $1' .tmp)
  if [[ -z "$rev" ]] ; then echo "skipping $pkg - no rev found"; continue ; fi
  srcprj=$(perl -ne 'm/project="([^"]+)"/ && print $1' .tmp)
  srcpkg=$(perl -ne 'm/package="([^"]+)"/ && print $1' .tmp)
  newprj=$rbbaseprj:rebuild:$srcpkg-$rev
  report=$rbbaseprj/reports/$srcpkg-$rev
  branchexists=$(if test_api_exists /public/source/$newprj ; then echo true ; else echo false ; fi )
  reportexists=$(if test_api_exists /public/source/$report ; then echo true ; else echo false ; fi )
  if $reportexists ; then # we are done ; move on to next pkg
      $branchexists && cleanup "$newprj"
      continue
  fi
  if ! $branchexists ; then
      setuplink
  else
      checkbranch
  fi
done
