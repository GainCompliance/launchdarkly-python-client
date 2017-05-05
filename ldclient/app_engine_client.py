import datetime

from ldclient.config import Config as Config
from ldclient.feature_requester import FeatureRequesterImpl
from ldclient.flag import evaluate
from ldclient.util import log


class LDAppEngineClient(object):
    def __init__(self, sdk_key=None, config=None):

        # TODO: make this not a copy/paste from client.py
        if config is not None and config.sdk_key is not None and sdk_key is not None:
            raise Exception("LaunchDarkly client init received both sdk_key and config with sdk_key. "
                            "Only one of either is expected")

        if sdk_key is not None:
            log.warn("Deprecated sdk_key argument was passed to init. Use config object instead.")
            self._config = Config(sdk_key=sdk_key)
        else:
            self._config = config or Config.default()
        self._config._validate()

        self._store = self._config.feature_store
        """ :type: FeatureStore """

        if self._config.offline:
            log.info("Started LaunchDarkly Client in offline mode")
            return

        self._feature_requester = FeatureRequesterImpl(self._config)
        """ :type: FeatureRequester """

        self._init_store()

        self._flag_expiry = datetime.datetime.utcnow() + datetime.timedelta(seconds=self._config.poll_interval)
        """ :type: datetime.datetime """

    def _init_store(self):
        log.debug('Initializing Features')
        self._store.init(self._feature_requester.get_all())
        """ :type: FeatureStore """

    def variation(self, key, user, default):
        default = self._config.get_default(key, default)

        # TODO: offline, validate user, TTL

        if datetime.datetime.utcnow() > self._flag_expiry:
            log.debug('Expiry reached, re-fetching features')
            # TODO: failure timeout here should not block returning
            self._init_store()

        def cb(flag):
            try:
                if not flag:
                    log.warn("Feature Flag key: " + key + " not found in Feature Store. Returning default.")
                    return default

                value, events = evaluate(flag, user, self._store)

                if value is None:
                    value = default

                return value
            except Exception as e:
                log.error("Exception caught in variation: " + e.message + " for flag key: " + key + " and user: " + str(user))

            return default

        return self._store.get(key, cb)
