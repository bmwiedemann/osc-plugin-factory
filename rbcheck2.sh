#!/bin/sh
# find all staging projects and run rbcheck1.sh on each of them
prjs=$(osc api "/search/project?match=starts_with(@name,'openSUSE:Factory:Staging:') and contains(@name,':adi:')" | perl -ne 'm/^\s*<project name="([^"]+)">/ && print $1,"\n"')
echo "found $prjs"
for prj in $prjs ; do
    echo "checking $prj for reproducible builds"
    ./rbcheck1.sh "$prj"
done
