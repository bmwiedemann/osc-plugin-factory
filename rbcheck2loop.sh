#!/bin/sh
mkdir -p state/reports state/prj
while sleep 9m ; do
    time ./rbcheck2.sh
done
