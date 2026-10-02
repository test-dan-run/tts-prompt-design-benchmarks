Optional custom vLLM-Omni deploy configs. Files here are copied to
`/opt/ttsbench/deploy/` in the backend image and can be referenced from
`config/models.yaml` with `--deploy-config /opt/ttsbench/deploy/<file>.yaml`.
Bundled configs that ship with vLLM-Omni (`vllm_omni/deploy/*.yaml`) are
loaded automatically for supported models and need nothing here.
