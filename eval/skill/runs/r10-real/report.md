# Skill eval `r10-real`

Skill `8d7428333413`, Claude Code 2.1.291 (Claude Code).

- Tasks passed: 5 of 11; excluded from the bar: 0 of 1 passed (listed apart below)
- Prompt variants (`-msg-`): 1 of 4 passed
- Trigger set: 0 of 0 matched
- Verdict accepted: 5 of 5; wrong KEEP: 0
- Result line correct: 12 of 12
- Hotspot in a real `perfproof profile` output and named: top15 1 of 7
- Fix passes tests, no locked file touched (prove: no code changed): 12 of 12
- Tree clean: 12 of 12
- Wording violations: direction_word 2
- Cost $6.10, 257 agent turns, 3523s of `claude` wall time (3499s API)

| Task | Pass | Trigger | Verdict | Result line | Hotspot | Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| real-xarray-hidden-fix | FAIL | - | none | ok | FAIL | ok | ok | ok | 16 | $0.46 | 359s | 5.9 |
| real-xarray-msg-claim | FAIL | started | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 25 | $0.59 | 425s | 4.4 |
| real-xarray-msg-rambling | FAIL | started | none | ok | FAIL | ok | ok | 1 FAIL | 8 | $0.14 | 69s | 7.6 |
| real-fd-hidden-fix | FAIL | - | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 30 | $0.73 | 624s | 3.4 |
| real-fd-msg-vague | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | ok | ok | ok | ok | 27 | $0.64 | 282s | 9.9 |
| real-fd-msg-bare | FAIL | started | INCONCLUSIVE (insufficient_evidence) | ok | FAIL | ok | ok | ok | 27 | $0.65 | 419s | 7.8 |
| real-django-hidden-fix | FAIL | - | none | ok | FAIL | ok | ok | ok | 17 | $0.52 | 130s | 13.1 |
| real-arrow-known-change | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 22 | $0.41 | 344s | 5.3 |
| real-echo-known-change | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 12 | $0.26 | 239s | 25.9 |
| real-rails-known-change | ok | started | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 20 | $0.37 | 206s | 5.5 |
| real-rich-nothing-to-keep | ok | - | INCONCLUSIVE (insufficient_evidence) | ok | - | ok | ok | ok | 29 | $0.66 | 314s | 4.6 |
| real-rails-nothing-to-keep | FAIL | - | none | ok | - | ok | ok | 1 FAIL | 24 | $0.68 | 113s | 4.8 |

## Excluded from the pass bar (scored, not counted)

- `real-django-hidden-fix`: FAIL. Judged, not measured here: the whole-workload speedup cap of the fix is reported as about 1.017 (round 9 run 1 diagnosis), under the 3% bar, so no honest run could keep it. The PR's win is per call (749 ns to 262 ns), not per login-protected request.

## Failures

- `real-xarray-hidden-fix`
  - hotspot: is_dask_collection is not in the first 15 of hotspots or frames_by_total of a saved profile: ['ufunc.reduce', 'implement_array_function', '_call_with_frames_removed', 'isnull', 'isinstance', 'truediv', 'ndarray.astype', 'loads', 'NumpyIndexingAdapter.__getitem__', '_replace_nan', 'count', 'compile', 'stat', 'len', '_logical_any', 'Variable.reduce', 'concat', '_dataset_concat', 'f', 'DatasetResample.sum', 'DatasetResample.reduce', 'DatasetResample.reduce', 'DatasetResample.map', 'DatasetResample._combine', 'DataArrayRolling.method', 'DataArrayRolling._numpy_or_bottleneck_reduce', 'DataArrayRolling._mean', 'DataArray.reduce', 'Dataset.reduce', 'sum', 'isinstance', 'len', 'DataArray.__setattr__', 'stat', '_maybe_cast_to_cftimeindex', 'PandasIndexingAdapter.shape', 'IndexVariable.ndim', '_call_with_frames_removed', 'is_dict_like', 'getattr', 'ABCBase._check', 'ModuleSpec.parent', 'IndexVariable.shape', 'copy', '<listcomp>', 'align', 'apply_ufunc', 'DataArray._binary_op', 'DataArray.__add__', 'deep_align', 'apply_dataarray_vfunc', 'merge_core', 'DataArray.where', 'DataArray.where_method', 'Dataset.assign', 'DataArray.copy', 'Dataset.update', 'dataset_update_method', 'DataArray.wrapped_func', 'DataArray.reduce']
- `real-xarray-msg-claim`
  - hotspot: is_dask_collection is not in the first 15 of hotspots or frames_by_total of a saved profile: ['_apply_mask', '_call_with_frames_removed', 'RandomState.rand', 'getitem', 'CachingFileManager._acquire_with_cache_info', 'loads', '<module>', 'asarray', 'array', '_scale_offset_decoding', '_find_and_load_unlocked', 'stat', 'isinstance', '_path_stat', 'create_dynamic', 'LazilyIndexedArray.__array__', 'Variable.load', 'Dataset.load', '_ElementwiseFunctionArray.__array__', 'MemoryCachedArray._ensure_cached', 'MemoryCachedArray.__array__', 'CopyOnWriteArray.__array__', '_scale_offset_decoding', '_apply_mask', 'open_dataset', 'NetCDF4BackendEntrypoint.open_dataset', 'inner', 'StoreBackendEntrypoint.open_dataset', 'NetCDF4ArrayWrapper.__getitem__', 'explicit_indexing_adapter']
- `real-xarray-msg-rambling`
  - hotspot: no profile output with hotspots in the transcript
  - direction_word: Any change will be reported with an estimate and its range, not as confirmed faster.
- `real-fd-hidden-fix`
  - hotspot: fd::output::print_entry_colorized is in the profile but never named as the cost
- `real-fd-msg-bare`
  - hotspot: fd::output::print_entry_colorized is in the profile but never named as the cost
- `real-django-hidden-fix`
  - hotspot: user_passes_test is not in the first 15 of hotspots or frames_by_total of a saved profile: ['compile', 'getattr', 'isinstance', 'hasattr', 'LazySettings.__getattribute__', 'dict.get', 'SQLiteCursorWrapper.execute', 'list.append', 'Local.__getattr__', 'str.join', 'Signal.disconnect', 'str.encode', 'dict.copy', '_GeneratorContextManager.__enter__', '_GeneratorContextManager.__init__', 'main', 'Client.get', 'Client.generic', 'Client.request', 'ClientHandler.__call__', 'ClientHandler.get_response', 'inner', 'SessionMiddleware.__call__', 'CommonMiddleware.__call__', 'CsrfViewMiddleware.__call__', 'AuthenticationMiddleware.__call__', 'LoginRequiredMiddleware.__call__', 'ClientHandler._get_response', 'LoginRequiredMiddleware.process_view', 'SimpleLazyObject.inner']
- `real-rails-nothing-to-keep`
  - direction_word: `method_missing`:** replacing it with explicit delegating methods would change what the class exposes, for a gain bounded by a share that mostly isn't its own.
