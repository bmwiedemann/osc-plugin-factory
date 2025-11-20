#!/bin/sh
mkdir -p state/reports
while sleep 9m ; do
    time ./rbcheck2.sh
done
