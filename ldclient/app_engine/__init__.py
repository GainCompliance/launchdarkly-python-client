import datetime
import jsonpickle
import time
import threading

import google.appengine.api.taskqueue as taskqueue
from ldclient.config import Config as Config
from ldclient.event_consumer import _headers as event_http_headers
from ldclient.feature_requester import FeatureRequesterImpl
from ldclient.flag import evaluate
from ldclient.util import log
import requests
from werkzeug.wsgi import get_path_info, get_input_stream

EVENTS_TASK_URL = '/_ldclient/events'

# noinspection PyBroadException
try:
    import queue
except:
    # noinspection PyUnresolvedReferences,PyPep8Naming
    import Queue as queue


_client = threading.local()


def get_client():
    return _client.value


def variation(key, user, default):
    return get_client().variation(key, user, default)


class LDAppEngine(object):

    """A WSGI middleware that ensures the LaunchDarkly client is initialized and torn down::

        from ldclient import LDAppEngine

        LDAppEngine(app, <CONFIG ARGS>)

        TODO: get rid of queueing and batch up events and send to a task queue
        TODO: check for special URL and process those events instead of routing to underlying app

    """

    def __init__(self, app, **kwargs):
        self.app = app.wsgi_app
        app.wsgi_app = self
        start = time.time()
        self.config = Config(**kwargs)
        self.client = LDAppEngineClient(config=self.config)
        log.debug('ld client initialized in %s ms' % str((time.time()-start)*1000))

    def __call__(self, environ, start_response):
        if get_path_info(environ) == EVENTS_TASK_URL:
            return self._handle_events(environ, start_response)

        global _client
        try:
            _client.value = self.client
            r = self.app(environ, start_response)
        finally:
            try:
                start = time.time()
                _client.value.enqueue_events()
                _client.value = None
                log.debug('flushed events to task queue in %s ms' % str((time.time()-start)*1000))
            except:
                log.exception('failure occurred while trying to send events to launchdarkly')
        return r

    def _handle_events(self, environ, start_response):
        hdrs = event_http_headers(self.config.sdk_key)
        uri = self.config.events_uri
        data = get_input_stream(environ).read()
        session = requests.Session()
        r = session.post(uri,
                         headers=hdrs,
                         timeout=(self.config.connect_timeout, self.config.read_timeout),
                         data=data)
        log.debug(r.text)
        log.debug(r.headers)
        r.raise_for_status()
        start_response('200 OK', [])
        return []


class LDAppEngineClient(object):
    def __init__(self, sdk_key=None, config=None, **kwargs):

        # TODO: make this not a copy/paste from client.py
        if config is not None and config.sdk_key is not None and sdk_key is not None:
            raise Exception("LaunchDarkly client init received both sdk_key and config with sdk_key. "
                            "Only one of either is expected")

        if sdk_key is not None:
            log.warn("Deprecated sdk_key argument was passed to init. Use config object instead.")
            self._config = Config(sdk_key=sdk_key, **kwargs)
        else:
            self._config = config or Config.default()
        self._config._validate()

        self._store = self._config.feature_store
        """ :type: FeatureStore """

        self._feature_requester = FeatureRequesterImpl(self._config)
        """ :type: FeatureRequester """

        self._init_store()

        self._events = []

    def _compute_new_expiry(self):
        return datetime.datetime.utcnow() + datetime.timedelta(seconds=self._config.poll_interval)

    def _init_store(self):
        log.debug('Initializing Features')
        self._store.init(self._feature_requester.get_all())
        """ :type: FeatureStore """
        self._flag_expiry = self._compute_new_expiry()
        """ :type: datetime.datetime """

    def variation(self, key, user, default):
        default = self._config.get_default(key, default)

        # TODO: offline, validate user
        try:
            current_ts = datetime.datetime.utcnow()
            if current_ts > self._flag_expiry:
                log.debug('expiry reached, re-fetching features (%s vs %s)' % (current_ts, self._flag_expiry))
                # TODO: failure timeout here should not block returning
                self._init_store()
        except Exception as e:
            log.exception(
                "Exception caught in initing store: for flag key: " + key + " and user: " + str(user))
            return default

        def store_event(value, version=None):
            self._store_event({'kind': 'feature', 'key': key,
                              'user': user, 'value': value, 'default': default, 'version': version})

        def cb(flag):
            try:
                if not flag:
                    log.warn("Feature Flag key: " + key + " not found in Feature Store. Returning default.")
                    store_event(default)
                    return default

                return self._evaluate_and_send_events(flag, user, default)
            except Exception as e:
                log.error("Exception caught in variation: " + e.message + " for flag key: " + key + " and user: " + str(user))

            return default

        return self._store.get(key, cb)

    def _evaluate_and_send_events(self, flag, user, default):
        value, events = self._evaluate(flag, user)
        for event in events or []:
            self._store_event(event)

        if value is None:
            value = default
        self._store_event({'kind': 'feature', 'key': flag.get('key'),
                          'user': user, 'value': value, 'default': default, 'version': flag.get('version')})
        return value

    def _evaluate(self, flag, user):
        return evaluate(flag, user, self._store)

    def _store_event(self, event):
        event['creationDate'] = int(time.time() * 1000)
        self._events.append(event)

    def enqueue_events(self):
        if not self._events:
            log.debug('no events to send')
            return
        log.debug('sending %s events to task queue' % len(self._events))
        payload = jsonpickle.encode(self._events)
        self._events = []
        t = taskqueue.Task(
            url=EVENTS_TASK_URL,
            payload=payload,
            method='POST',
            retry_options=taskqueue.TaskRetryOptions(task_retry_limit=3),
            namespace=''
        )
        t.add()
