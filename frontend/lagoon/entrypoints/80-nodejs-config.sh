#!/bin/sh

sed "s|\${REACT_APP_API_URL}|$REACT_APP_API_URL|g" /app/build/config.js > /app/build/config.js