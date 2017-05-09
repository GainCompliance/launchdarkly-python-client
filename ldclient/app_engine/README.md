LaunchDarkly on Google App Engine (Standard)
===========================

The regular python client use of background threads makes it unusable in
[Google App Engine Standard](https://cloud.google.com/appengine/docs/standard/python/).  This package attempts to 
provide support within the sandbox of GAE.  
 
Usage
-----

1) Follow [instructions for using requests library on GAE](https://cloud.google.com/appengine/docs/standard/python/issue-requests#issuing_an_https_request)
2) Ensure Flask has been pip installed in your application
3) `app.yaml`:

        `threadsafe: no`
4) Wrap your Flask app like:
```
from ldclient.app_engine import LDAppEngine
LDAppEngine(app, sdk_key=<YOUR SDK KEY>, poll_interval=<DESIRED TTL IN SECONDS>)
```

Todo
----
* **Persistent Cache and Streaming Updates:** 
Currently each app engine instance uses an in-memory store that is built upon instance startup. 
Ideally this could be put into memcache or datastore and kept up to date by a cron.
* **More Framework Support:** It's currently written as a WSGI middleware meant for
Flask.



