# Ollama MLX Evaluation 2026-03-31

Timestamp: `2026-03-31 21:30:08 CEST`

## Scope

This evaluation checked whether the new Ollama runtime on Apple Silicon powered by MLX can materially improve the `mAIl` email-analysis flow.

The review covered:

- official changes in Ollama on Apple Silicon
- impact on the `mAIl` use case
- a local benchmark on the current production-style model path
- hardware and memory implications on this Mac
- business relevance, limitations, and next steps

The current `mAIl` LLM path is implemented in [src/mail_ai_agent/llm_gateway.py](/Users/gniewkob/Repos/priv/mAIl/src/mail_ai_agent/llm_gateway.py):

- endpoint: `/api/generate`
- model: `qwen3:8b`
- mode: `stream=false`
- output: `format="json"`
- temperature: `0.1`

## What Changed in Ollama

As of `2026-03-30`, Ollama publicly announced a preview runtime for Apple Silicon:

- blog entry: `Ollama is now powered by MLX on Apple Silicon in preview`

The developer documentation also confirms:

- Ollama now has an optional `MLX Engine`
- the MLX engine is built separately
- on macOS Apple Silicon it uses the Metal toolchain
- the MLX engine is intended for `safetensors`-based models

MLX itself uses Apple Silicon unified memory:

- arrays live in shared memory
- operations can run on CPU or GPU without performing data copies between them

This matters because the current local `mAIl` baseline is not using the MLX engine. It is using the standard Ollama runner with GGUF models and full Metal offload.

Sources:

- Ollama blog index: <https://registry.ollama.com/blog>
- Ollama development docs: <https://docs.ollama.com/development>
- Ollama structured outputs docs: <https://docs.ollama.com/capabilities/structured-outputs>
- MLX docs: <https://ml-explore.github.io/mlx/build/html/>

## Local Hardware and Baseline

The benchmark machine is:

- `Apple M4 Pro`
- `24 GiB` unified memory

Installed local models include:

- `qwen3:8b`
- `mistral-nemo:12b`
- `qwen2.5-coder:7b`
- `llama3.1:8b`

Current observed Ollama runtime for `qwen3:8b`:

- footprint in `ollama ps`: `5.9 GB`
- processor: `100% GPU`
- context: `4096`
- log-reported model memory: about `5.5 GiB`
- full GPU offload: `37/37` layers

## Benchmark / PoC

### Benchmark setup

I ran a direct benchmark against the live local Ollama server using the same `mAIl` prompt shape and `qwen3:8b`.

The sample set contained `6` representative emails:

- price question
- appointment reschedule
- billing / invoice
- complaint
- spam / offer
- partner routing request

Measured dimensions:

- wall-clock latency
- approximate TTFT
- prompt throughput
- decode throughput
- schema validity against `LLMClassification`
- coarse category accuracy on the sample set

### Results

Summary:

- schema-valid rate: `100%` (`6/6`)
- category match rate: `83.3%` (`5/6`)
- mean latency: `5855 ms`
- median latency: `4975 ms`
- mean approximate TTFT: `1260 ms`
- warm approximate TTFT: about `500 ms`
- mean decode speed: `34.99 tok/s`
- mean prompt throughput: `997.97 tok/s`

Per-request notes:

- the first request had a heavy cold-start penalty:
  - wall-clock `10143 ms`
  - approximate TTFT `5005 ms`
  - `load_duration` `2675 ms`
- warm requests mostly landed around:
  - `4.1s` to `6.3s`
  - approximate TTFT `~500 ms`
  - decode speed `~34-36 tok/s`

### Quality notes

The current baseline quality is usable for classification:

- `question`, `appointment`, `billing`, `complaint`, and `spam_or_offer` were classified correctly
- one routing-style message was misclassified as `appointment` instead of `other`

This is important for `mAIl`: the current bottleneck is not only runtime speed. Model behavior and schema discipline still matter.

## Long-Context Risk

I also tested a long synthetic thread to check truncation behavior.

Observed:

- with a long but still manageable mail, the model still returned a correct complaint classification
- with a much longer prompt (`61434` characters), Ollama truncated prompt evaluation to `4096` tokens
- under that pressure, the model returned the wrong output shape:
  - `{"query": "..."}`
  - not the `LLMClassification` schema

Implication:

- the current `uncertain` risk for long or messy threads remains real
- MLX may improve speed, but it does not solve schema drift or truncation by itself

## Impact on the `mAIl` Use Case

### 1. Classification

Potential benefit: `medium`

- warm latency is already acceptable for short single-email classification
- MLX could reduce cold-start and improve throughput
- the business effect depends on how often mail reaches the LLM instead of deterministic rules

### 2. Intent / topic / entity extraction

Potential benefit: `medium to high`

- these tasks benefit more from faster decode speed and better local concurrency
- still gated by structured output reliability

### 3. Summaries

Potential benefit: `high`

- summary generation is more generation-heavy than classification-only output
- faster decode has clearer user-facing value here

### 4. Routing to categories or teams

Potential benefit: `medium`

- routing latency improves only if the runtime is the dominant cost
- the larger risk today is wrong category choice on ambiguous messages

### 5. Priority / sentiment / action required

Potential benefit: `medium`

- these enrichments fit the same schema-based flow
- speed helps, but reliability and determinism matter more than raw tokens/sec

## Business Evaluation

### Where it helps

- lower latency for local AI analysis on Apple Silicon
- better chance to scale local-only processing without using external services
- stronger privacy posture because mail stays local
- potentially better UX when more mail types use LLM rather than just rule fallback

### Where it does not help enough by itself

- it does not directly fix `uncertain` caused by invalid structured output
- it does not remove context-window limits
- it does not guarantee better classification quality

### Practical business reading

For `mAIl`, MLX is probably not the first lever to pull.

The first lever is:

- make structured outputs stricter and more deterministic

The second lever is:

- test MLX preview on the same replay set once a matching model artifact is available

## Risks and Limitations

### Preview stability

- this is still a preview feature
- production rollout risk is higher than with the current stable path

### Model format mismatch

- the MLX engine is documented for `safetensors`
- the current production-style path uses `qwen3:8b` as `GGUF` `Q4_K_M`
- this means A/B is not a perfect apples-to-apples swap yet

### Context limits

- the current observed context for `qwen3:8b` is `4096`
- long threads can still be truncated
- truncation already causes schema breakage in our flow

### Hardware differences across Macs

- this Mac has `24 GiB` unified memory and handles `8B` comfortably
- smaller Macs will have tighter concurrency and model-size ceilings
- larger models like `12B+` have visibly higher load and memory cost

### Security / privacy / deployment

- privacy remains a strength: inference is local
- deployment becomes more complex if MLX preview requires different model artifacts or a separate engine setup
- reproducibility and rollback need to be part of the evaluation plan

## Recommendation

### Recommendation status

`Worth testing further, but not worth immediate production migration.`

### Why

The current `mAIl` workflow is not primarily blocked by inference speed. It is more constrained by:

- schema stability
- long-thread truncation
- ambiguous-message quality

MLX can still be valuable, especially if we want:

- more local throughput
- lower warm latency under sustained load
- better UX for summary-heavy or extraction-heavy paths

### Most sensible next step

1. In `mAIl`, switch from plain `format="json"` to schema-based structured outputs using `LLMClassification.model_json_schema()`.
2. Set temperature to `0` for the classification path.
3. Build a replay benchmark harness from real redacted mail samples.
4. Run the same replay on:
   - current stable Ollama path
   - MLX preview path on Apple Silicon
5. Compare:
   - warm latency
   - cold-start latency
   - decode throughput
   - schema-valid rate
   - `uncertain` rate

## Final Conclusion

The new Ollama MLX path is technically relevant for `mAIl`, especially on Apple Silicon Macs. It has clear potential to improve local LLM throughput and reduce some latency costs. However, based on the current `mAIl` flow and this local benchmark, the highest-value improvement is still structured-output reliability, not raw runtime speed.

MLX should be treated as a targeted optimization track after the structured-output path is tightened.
