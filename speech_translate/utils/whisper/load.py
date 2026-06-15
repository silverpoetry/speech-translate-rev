import json
from dataclasses import dataclass, field
from threading import Lock

from speech_translate.log_helpers import logger
from speech_translate.runtime_deps import (
    get_faster_whisper_model_class,
    get_stable_whisper,
    get_torch,
)
from speech_translate.utils.types import SettingDict
from speech_translate.utils.whisper.paths import get_default_download_root

from .helper import get_temperature
from .stable_args import parse_args_stable_ts as _parse_args_stable_ts


@dataclass(frozen=True)
class ModelLoadPlan:
    tc_model_name: str | None
    tl_model_name: str | None
    reuse_tc_for_tl: bool = False


@dataclass(frozen=True)
class WhisperLoadRuntime:
    stable_whisper_api: object
    torch_api: object
    faster_whisper_model_type: object


@dataclass(frozen=True)
class ModelVariantCacheKey:
    backend: str
    model_name: str
    model_args_key: str

    @classmethod
    def create(cls, model_name: str, use_faster_whisper: bool, model_args: dict) -> "ModelVariantCacheKey":
        return cls(
            backend="faster-whisper" if use_faster_whisper else "whisper",
            model_name=model_name,
            model_args_key=_freeze_model_args(model_args),
        )


@dataclass(frozen=True)
class ModelBundleRequest:
    transcribe: bool
    translate: bool
    tl_engine_whisper: bool
    model_name_tc: str
    engine: str
    use_faster_whisper: bool
    model_args_key: str

    @property
    def backend_label(self) -> str:
        return "faster-whisper" if self.use_faster_whisper else "whisper"

    @classmethod
    def create(
        cls,
        *,
        transcribe: bool,
        translate: bool,
        tl_engine_whisper: bool,
        model_name_tc: str,
        engine: str,
        use_faster_whisper: bool,
        model_args: dict,
    ) -> "ModelBundleRequest":
        return cls(
            transcribe=transcribe,
            translate=translate,
            tl_engine_whisper=tl_engine_whisper,
            model_name_tc=model_name_tc,
            engine=engine,
            use_faster_whisper=use_faster_whisper,
            model_args_key=_freeze_model_args(model_args),
        )


@dataclass
class WhisperModelCacheStore:
    model_cache: dict[ModelVariantCacheKey, object] = field(default_factory=dict)
    bundle_cache: dict[ModelBundleRequest, tuple] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)


_GLOBAL_MODEL_CACHE_STORE = WhisperModelCacheStore()
# Global model cache shared across realtime/file/preload flows.
_MODEL_CACHE = _GLOBAL_MODEL_CACHE_STORE.model_cache
_MODEL_CACHE_LOCK = _GLOBAL_MODEL_CACHE_STORE.lock
_MODEL_BUNDLE_CACHE = _GLOBAL_MODEL_CACHE_STORE.bundle_cache


def _get_stable_whisper_api():
    return get_stable_whisper()


def _get_torch_api():
    return get_torch()


def _get_faster_whisper_model_type():
    return get_faster_whisper_model_class()


def _build_whisper_load_runtime() -> WhisperLoadRuntime:
    return WhisperLoadRuntime(
        stable_whisper_api=_get_stable_whisper_api(),
        torch_api=_get_torch_api(),
        faster_whisper_model_type=_get_faster_whisper_model_type(),
    )


def _freeze_model_args(model_args: dict) -> str:
    """Create a stable, hashable representation for model loading arguments."""
    return json.dumps(model_args, sort_keys=True, default=str)


def _load_model_cached(
    model_name: str,
    use_faster_whisper: bool,
    *,
    runtime: WhisperLoadRuntime | None = None,
    cache_store: WhisperModelCacheStore = _GLOBAL_MODEL_CACHE_STORE,
    **model_args,
):
    """Load model once per (backend, model, args) and reuse globally."""
    runtime = runtime or _build_whisper_load_runtime()
    cache_key = ModelVariantCacheKey.create(model_name, use_faster_whisper, model_args)

    with cache_store.lock:
        cached = cache_store.model_cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Model cache hit: backend={cache_key.backend} model={model_name}")
            return cached

    logger.debug(f"Model cache miss: backend={cache_key.backend} model={model_name}; loading model")
    stable_whisper_api = runtime.stable_whisper_api
    if use_faster_whisper:
        if _is_faster_whisper_model_cached(model_name, model_args.get("download_root", get_default_download_root())):
            model_args = dict(model_args)
            model_args["local_files_only"] = True
    if use_faster_whisper:
        loaded = stable_whisper_api.load_faster_whisper(model_name, **model_args)
    else:
        loaded = stable_whisper_api.load_model(model_name, **model_args)

    with cache_store.lock:
        # Double-check in case another thread loaded the same model while we were loading.
        existing = cache_store.model_cache.get(cache_key)
        if existing is not None:
            return existing
        cache_store.model_cache[cache_key] = loaded
        return loaded


def _load_model_variant(
    model_name: str,
    use_faster_whisper: bool,
    *,
    runtime: WhisperLoadRuntime | None = None,
    cache_store: WhisperModelCacheStore = _GLOBAL_MODEL_CACHE_STORE,
    **model_args,
):
    """Load a model and return the matching transcription entry point."""
    model = _load_model_cached(
        model_name,
        use_faster_whisper,
        runtime=runtime,
        cache_store=cache_store,
        **model_args,
    )
    runner_name = "transcribe_stable" if use_faster_whisper else "transcribe"
    return model, getattr(model, runner_name)


def _build_model_load_plan(
    transcribe: bool,
    translate: bool,
    tl_engine_whisper: bool,
    model_name_tc: str,
    engine: str,
) -> ModelLoadPlan:
    if not model_name_tc:
        return ModelLoadPlan(tc_model_name=None, tl_model_name=None, reuse_tc_for_tl=False)

    if transcribe and translate and tl_engine_whisper and model_name_tc == engine:
        return ModelLoadPlan(tc_model_name=model_name_tc, tl_model_name=None, reuse_tc_for_tl=True)

    tc_model_name = model_name_tc if transcribe or (translate and not tl_engine_whisper) else None
    tl_model_name = engine if translate and tl_engine_whisper else None
    return ModelLoadPlan(tc_model_name=tc_model_name, tl_model_name=tl_model_name, reuse_tc_for_tl=False)


def _execute_model_load_plan(
    plan: ModelLoadPlan,
    *,
    runtime: WhisperLoadRuntime | None = None,
    cache_store: WhisperModelCacheStore = _GLOBAL_MODEL_CACHE_STORE,
    use_faster_whisper: bool,
    model_args: dict,
):
    runtime = runtime or _build_whisper_load_runtime()
    backend_label = "faster-whisper" if use_faster_whisper else "whisper"
    model_tc, model_tl, stable_tc, stable_tl = None, None, None, None

    if plan.reuse_tc_for_tl and plan.tc_model_name:
        logger.debug(f"Loading model for both transcribe and translate using {backend_label} | Load only once")
    else:
        if plan.tc_model_name is not None:
            logger.debug(f"Loading model for transcribe using {backend_label}")
        if plan.tl_model_name is not None:
            logger.debug(f"Loading model for translate using {backend_label}")

    if plan.tc_model_name is not None:
        model_tc, stable_tc = _load_model_variant(
            plan.tc_model_name,
            use_faster_whisper,
            runtime=runtime,
            cache_store=cache_store,
            **model_args,
        )

    if plan.reuse_tc_for_tl:
        stable_tl = stable_tc
    elif plan.tl_model_name is not None:
        model_tl, stable_tl = _load_model_variant(
            plan.tl_model_name,
            use_faster_whisper,
            runtime=runtime,
            cache_store=cache_store,
            **model_args,
        )

    return model_tc, model_tl, stable_tc, stable_tl


def _bundle_cache_key(
    transcribe: bool,
    translate: bool,
    tl_engine_whisper: bool,
    model_name_tc: str,
    engine: str,
    use_faster_whisper: bool,
    model_args: dict,
):
    return ModelBundleRequest.create(
        transcribe=transcribe,
        translate=translate,
        tl_engine_whisper=tl_engine_whisper,
        model_name_tc=model_name_tc,
        engine=engine,
        use_faster_whisper=use_faster_whisper,
        model_args=model_args,
    )


def is_model_bundle_cached(
    transcribe: bool,
    translate: bool,
    tl_engine_whisper: bool,
    model_name_tc: str,
    engine: str,
    setting_cache: SettingDict,
    **model_args,
) -> bool:
    """Check whether an equivalent get_model bundle is already cached globally."""
    key = _bundle_cache_key(
        transcribe,
        translate,
        tl_engine_whisper,
        model_name_tc,
        engine,
        setting_cache["use_faster_whisper"],
        model_args,
    )
    with _GLOBAL_MODEL_CACHE_STORE.lock:
        return key in _GLOBAL_MODEL_CACHE_STORE.bundle_cache

def parse_args_stable_ts(arguments: str, mode: str, method=None, **kwargs):
    return _parse_args_stable_ts(arguments, mode, method, **kwargs)


def get_tc_args(process_func, setting_cache: SettingDict, mode="transcribe"):
    """
    Get arguments / parameter to load to stable ts 
    for transcribe / translate using whisper and get their respective function

    Parameters
    ----------
    model_name_tc : str
        The model name for transcribe / translate
    lang_source : str
        The source language
    auto : bool
        Wether the source language is auto or not
    setting_cache : SettingDict
        The setting value

    Returns
    -------
    tuple of dict, function, function
        The parameter / argument to load to stable ts, the transcribe function, and the translate function

    Raises
    ------
    Exception
        If temperature is not valid will throw exception containing the failure message
    Exception
        If the model args is not valid will throw exception containing the failure message
    """
    temperature = setting_cache["temperature"]
    success, data = get_temperature(temperature)
    if not success:
        raise Exception(data)
    else:
        temperature = data

    try:
        suppress_tokens = [int(x) for x in setting_cache["suppress_tokens"].split(",")]
    except Exception:
        # suppres token in the setting is saved as string
        # if fail to parse, it means that the suppress_tokens is set to empty
        # if empty, faster whisper needs to be set to None
        if "faster_whisper" in str(process_func):
            suppress_tokens = None
        else:
            suppress_tokens = setting_cache["suppress_tokens"]

    # parse whisper_args
    pass_kwarg = {
        "temperature": temperature,
        "best_of": setting_cache["best_of"],
        "beam_size": setting_cache["beam_size"],
        "patience": setting_cache["patience"],
        "compression_ratio_threshold": setting_cache["compression_ratio_threshold"],
        "logprob_threshold": setting_cache["logprob_threshold"],
        "no_speech_threshold": setting_cache["no_speech_threshold"],
        "suppress_tokens": suppress_tokens,
        "suppress_blank": setting_cache["suppress_blank"],
        "initial_prompt": setting_cache["initial_prompt"],
        "prefix": setting_cache["prefix"],
        "condition_on_previous_text": setting_cache["condition_on_previous_text"],
        "max_initial_timestamp": setting_cache["max_initial_timestamp"],
        "fp16": setting_cache["fp16"],
    }
    logger.debug("Pass kwarg:")
    logger.debug(pass_kwarg)
    data = parse_args_stable_ts(setting_cache["whisper_args"], mode, process_func, **pass_kwarg)
    if not data.pop("success"):
        raise Exception(data["msg"])
    else:
        whisper_args = data
        threads = whisper_args.pop("threads")
        if threads:
            _get_torch_api().set_num_threads(threads)

    return whisper_args


def get_model(
    transcribe: bool, translate: bool, tl_engine_whisper: bool, model_name_tc: str, engine: str, setting_cache: SettingDict,
    **model_args
):
    """Get model and the function for stable whisper while also checking using faster whisper or not

    Parameters
    ----------
    transcribe : bool
        Transcribe or not
    translate : bool
        Translate or not
    tl_engine_whisper : bool
        Translate using whisper or not
    model_name_tc : str
        Name of the transcription model
    engine : str
        engine name
    setting_cache : SettingDict
        Setting value

    Returns
    -------
    tuple
        model_tc, model_tl, stable_tc, stable_tl, load_to_tc_args
    """
    model_tc, model_tl, stable_tc, stable_tl = None, None, None, None
    use_faster_whisper = setting_cache["use_faster_whisper"]
    runtime = _build_whisper_load_runtime()
    bundle_key = _bundle_cache_key(
        transcribe,
        translate,
        tl_engine_whisper,
        model_name_tc,
        engine,
        use_faster_whisper,
        model_args,
    )

    with _GLOBAL_MODEL_CACHE_STORE.lock:
        cached_bundle = _GLOBAL_MODEL_CACHE_STORE.bundle_cache.get(bundle_key)
        if cached_bundle is not None:
            logger.debug(
                "Model bundle cache hit: "
                f"tc={bundle_key.model_name_tc} engine={bundle_key.engine} faster={bundle_key.use_faster_whisper}"
            )
            return cached_bundle
    plan = _build_model_load_plan(
        transcribe,
        translate,
        tl_engine_whisper,
        model_name_tc,
        engine,
    )
    model_tc, model_tl, stable_tc, stable_tl = _execute_model_load_plan(
        plan,
        runtime=runtime,
        cache_store=_GLOBAL_MODEL_CACHE_STORE,
        use_faster_whisper=use_faster_whisper,
        model_args=model_args,
    )

    load_to_tc_args = stable_tc if stable_tc is not None else stable_tl  # making sure that the load_to_tc_args is not None

    logger.debug(f"Model loaded | Is Faster Whisper: {setting_cache['use_faster_whisper']} | Load Status:")
    logger.debug(f"TC: {'Set' if model_tc else 'Not Set'}")
    logger.debug(f"TL: {'Set' if model_tl else 'Not Set'}")
    logger.debug(f"func_tc: {'Set' if stable_tc else 'Not Set'}")
    logger.debug(f"func_tl: {'Set' if stable_tl else 'Not Set'}")

    bundle = (model_tc, model_tl, stable_tc, stable_tl, load_to_tc_args)
    with _GLOBAL_MODEL_CACHE_STORE.lock:
        _GLOBAL_MODEL_CACHE_STORE.bundle_cache[bundle_key] = bundle
    return bundle


def _resolve_model_download_root(setting_cache: SettingDict) -> str:
    if setting_cache["dir_model"] != "auto":
        return setting_cache["dir_model"]
    return get_default_download_root()


def _is_faster_whisper_model_cached(model_name: str, download_root: str) -> bool:
    try:
        from speech_translate.utils.whisper.download import verify_model_faster_whisper

        return bool(verify_model_faster_whisper(model_name, download_root))
    except Exception:
        return False


def _resolve_model_device(setting_cache: SettingDict, *, cuda_available: bool) -> str:
    device_pref = str(setting_cache.get("model_device_preference", "auto") or "auto").strip().lower()
    if device_pref not in {"auto", "cpu", "cuda"}:
        device_pref = "auto"

    if device_pref == "cpu":
        return "cpu"
    if device_pref == "cuda":
        if cuda_available:
            return "cuda"
        logger.warning("model_device_preference=cuda but CUDA is unavailable; falling back to CPU")
        return "cpu"
    return "cuda" if cuda_available else "cpu"


def get_model_args(setting_cache: SettingDict):
    """Get arguments / parameter to load to stable ts

    Parameters
    ----------
    setting_cache: dict
        Setting value

    Returns
    -------
    dict
       The parameter / argument to load to stable ts

    Raises
    ------
    Exception
        If the model args is not valid will throw exception containing the failure message
    """
    runtime = _build_whisper_load_runtime()
    load_target = runtime.faster_whisper_model_type if setting_cache["use_faster_whisper"] else runtime.stable_whisper_api.load_model

    # load model
    model_args = parse_args_stable_ts(
        setting_cache["whisper_args"], "load",
        load_target,
    )
    if not model_args.pop("success"):
        raise Exception(model_args["msg"])

    model_args["download_root"] = _resolve_model_download_root(setting_cache)
    model_args["device"] = _resolve_model_device(
        setting_cache,
        cuda_available=runtime.torch_api.cuda.is_available(),
    )

    return model_args
