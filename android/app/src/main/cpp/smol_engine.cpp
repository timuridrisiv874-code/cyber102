#include <cstdio>
#include <cstdlib>
#include "smol_engine.h"
#include "llama.h"
#include "mtmd.h"
#include "mtmd-helper.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <fstream>
#include <iomanip>
#include <limits>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <vector>

#if defined(__ANDROID__) || defined(CYBER102_TEST_VISUAL_FINDINGS)
#define CYBER_VISUAL_FINDINGS
#include "visionpsy_findings_weights.h"
#endif

namespace cyber_smol {
namespace {
using Clock = std::chrono::steady_clock;
std::mutex operation_mutex;
std::once_flag backend_once;
std::atomic<bool> loaded(false);
std::atomic<std::uint64_t> cancel_epoch{0};
std::uint64_t active_epoch = 0;
constexpr int CONTEXT_SIZE = 2048;
constexpr int BATCH_SIZE = 256;
constexpr std::size_t MAX_IMAGE_BYTES = 3 * 1024 * 1024;

void quiet_log(ggml_log_level level, const char * text, void *) { if (level >= GGML_LOG_LEVEL_WARN) std::fputs(text, stderr); }
bool is_cancelled() { return cancel_epoch.load() != active_epoch; }
#if !defined(CYBER_VISUAL_FINDINGS)
bool abort_decode(void *) { return is_cancelled(); }
#endif
bool load_progress(float, void *) { return !is_cancelled(); }
bool graph_progress(ggml_tensor * tensor, bool ask, void *) {
    // Schedule checks before computing the graph. A stop requested mid-encode
    // is then observed at the next matrix operation rather than after the graph.
    return ask ? tensor->op == GGML_OP_MUL_MAT : !is_cancelled();
}
double milliseconds(Clock::time_point start, Clock::time_point end) {
    return std::chrono::duration<double, std::milli>(end - start).count();
}

struct Engine {
    llama_model * model = nullptr;
    llama_context * context = nullptr;
    mtmd_context * vision = nullptr;
    ~Engine() {
        if (vision) mtmd_free(vision);
        if (context) llama_free(context);
        if (model) llama_model_free(model);
    }
};
std::unique_ptr<Engine> engine;

std::string escape_json(const std::string & value) {
    std::ostringstream out;
    for (unsigned char c : value) {
        switch (c) {
            case '"': out << "\\\""; break;
            case '\\': out << "\\\\"; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:
                if (c < 32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c) << std::dec;
                else out << char(c);
        }
    }
    return out.str();
}

std::vector<unsigned char> read_image(const std::string & path) {
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    if (!file) throw std::runtime_error("Image file is unavailable");
    const auto size = file.tellg();
    if (size < 3 || static_cast<std::size_t>(size) > MAX_IMAGE_BYTES) throw std::runtime_error("Image exceeds 3 MB limit");
    std::vector<unsigned char> bytes(static_cast<std::size_t>(size));
    file.seekg(0);
    if (!file.read(reinterpret_cast<char *>(bytes.data()), size)) throw std::runtime_error("Cannot read image");
    const bool jpeg = bytes[0] == 0xff && bytes[1] == 0xd8 && bytes[2] == 0xff;
    const unsigned char png_magic[] = {0x89, 'P', 'N', 'G', 13, 10, 26, 10};
    const bool png = bytes.size() >= 24 && std::equal(std::begin(png_magic), std::end(png_magic), bytes.begin());
    if (!jpeg && !png) throw std::runtime_error("Only normalized JPEG/PNG frames are accepted");
    // Bound dimensions before the image decoder allocates a pixel buffer.
    unsigned int width = 0, height = 0;
    if (png) {
        for (int i = 0; i < 4; ++i) {
            width = (width << 8) | bytes[16 + i];
            height = (height << 8) | bytes[20 + i];
        }
    } else {
        std::size_t pos = 2;
        while (pos + 3 < bytes.size()) {
            if (bytes[pos++] != 0xff) throw std::runtime_error("Invalid JPEG segment");
            while (pos < bytes.size() && bytes[pos] == 0xff) ++pos;
            if (pos >= bytes.size()) break;
            const unsigned int marker = bytes[pos++];
            if (marker == 0xd9 || marker == 0xda) break;
            if (marker == 0x01 || (marker >= 0xd0 && marker <= 0xd8)) continue;
            if (pos + 1 >= bytes.size()) break;
            const std::size_t size = (bytes[pos] << 8) | bytes[pos + 1];
            if (size < 2 || size > bytes.size() - pos) throw std::runtime_error("Invalid JPEG length");
            if (marker >= 0xc0 && marker <= 0xcf && marker != 0xc4 && marker != 0xc8 && marker != 0xcc) {
                if (size < 8) throw std::runtime_error("Invalid JPEG dimensions");
                height = (bytes[pos + 3] << 8) | bytes[pos + 4];
                width = (bytes[pos + 5] << 8) | bytes[pos + 6];
                break;
            }
            pos += size;
        }
    }
    if (!width || !height || width > 1280 || height > 1280) throw std::runtime_error("Normalize the image to at most 1280 pixels per side");
    return bytes;
}

std::string format_prompt(const std::string & prompt) {
    const std::string content = std::string(mtmd_default_marker()) + "\n" + prompt;
    llama_chat_message message{"user", content.c_str()};
    // The pinned VisionPsy model uses ChatML. This avoids a
    // general template/Jinja dependency while preserving its turn markers.
    std::vector<char> buffer(content.size() * 2 + 256);
    int length = llama_chat_apply_template("chatml", &message, 1, true, buffer.data(), buffer.size());
    if (length < 0) throw std::runtime_error("Unsupported VisionPsy chat template");
    if (static_cast<std::size_t>(length) > buffer.size()) {
        buffer.resize(length);
        length = llama_chat_apply_template("chatml", &message, 1, true, buffer.data(), buffer.size());
    }
    return std::string(buffer.data(), length);
}

#if !defined(CYBER_VISUAL_FINDINGS)
std::string token_piece(const llama_vocab * vocab, llama_token token) {
    char small[128];
    int length = llama_token_to_piece(vocab, token, small, sizeof(small), 0, false);
    if (length >= 0) return std::string(small, length);
    std::vector<char> buffer(-length);
    length = llama_token_to_piece(vocab, token, buffer.data(), buffer.size(), 0, false);
    if (length < 0) throw std::runtime_error("Cannot decode output token");
    return std::string(buffer.data(), length);
}
#else
std::string classify_frame(const mtmd_input_chunks * chunks) {
    constexpr size_t dim = visionpsy_findings::embedding_size;
    std::array<float, dim> sum{};
    std::array<float, dim> maximum;
    maximum.fill(-std::numeric_limits<float>::infinity());
    size_t token_count = 0;
    for (size_t i = 0; i < mtmd_input_chunks_size(chunks) && !is_cancelled(); ++i) {
        const auto * chunk = mtmd_input_chunks_get(chunks, i);
        if (mtmd_input_chunk_get_type(chunk) != MTMD_INPUT_CHUNK_TYPE_IMAGE) continue;
        if (mtmd_encode_chunk(engine->vision, chunk) != 0) {
            if (is_cancelled()) return {};
            throw std::runtime_error("Local image encoding failed");
        }
        const size_t count = mtmd_input_chunk_get_n_tokens(chunk);
        const float * values = mtmd_get_output_embd(engine->vision);
        if (!values || !count) throw std::runtime_error("VisionPsy returned no image tokens");
        token_count += count;
        for (size_t token = 0; token < count; ++token) {
            for (size_t j = 0; j < dim; ++j) {
                sum[j] += values[token * dim + j];
                maximum[j] = std::max(maximum[j], values[token * dim + j]);
            }
        }
    }
    if (is_cancelled()) return {};
    if (!token_count) throw std::runtime_error("VisionPsy returned no image tokens");
    std::string findings;
    for (const auto & label : visionpsy_findings::labels) {
        float logit = label.bias;
        for (size_t j = 0; j < dim; ++j) {
            logit += label.weights[j] * (sum[j] / token_count) + label.weights[dim + j] * maximum[j];
        }
        if (logit >= label.threshold_logit) {
            if (!findings.empty()) findings += '\n';
            findings += label.text;
        }
    }
    return findings.empty() ? "unclear" : findings;
}
#endif
}

std::string Result::json() const {
    std::ostringstream out;
    out << std::fixed << std::setprecision(3)
        << "{\"text\":\"" << escape_json(text) << "\",\"promptMs\":" << prompt_ms
        << ",\"generateMs\":" << generate_ms << ",\"totalMs\":" << total_ms
        << ",\"generatedTokens\":" << generated_tokens << ",\"inputTokens\":" << input_tokens
        << ",\"visualFindings\":" << (visual_findings ? "true" : "false")
        << ",\"cancelled\":" << (cancelled ? "true" : "false") << "}";
    return out.str();
}

void load(const std::string & model_path, const std::string & projector_path, int threads, std::uint64_t epoch) {
    std::lock_guard<std::mutex> lock(operation_mutex);
    active_epoch = epoch;
    if (is_cancelled()) throw std::runtime_error("Model loading cancelled");
    loaded.store(false);
    engine.reset();
    std::call_once(backend_once, [] {
        setenv("MTMD_NO_UPSCALE", "1", 1);
        llama_log_set(quiet_log, nullptr);
        mtmd_helper_log_set(quiet_log, nullptr);
        llama_backend_init();
    });
    auto next = std::make_unique<Engine>();
    auto model_params = llama_model_default_params();
    model_params.n_gpu_layers = 0;
    model_params.progress_callback = load_progress;
    model_params.progress_callback_user_data = nullptr;
    next->model = llama_model_load_from_file(model_path.c_str(), model_params);
    if (!next->model) throw std::runtime_error(is_cancelled() ? "Model loading cancelled" : "Cannot load local SmolVLM weights");
    threads = std::clamp(threads, 2, 6);
#if !defined(CYBER_VISUAL_FINDINGS)
    auto context_params = llama_context_default_params();
    context_params.n_ctx = CONTEXT_SIZE;
    context_params.n_batch = BATCH_SIZE;
    context_params.n_ubatch = BATCH_SIZE;
    context_params.n_threads = threads;
    context_params.n_threads_batch = threads;
    context_params.offload_kqv = false;
    context_params.abort_callback = abort_decode;
    context_params.abort_callback_data = nullptr;
    next->context = llama_init_from_model(next->model, context_params);
    if (!next->context) throw std::runtime_error("Insufficient memory for SmolVLM context");
#endif
    auto vision_params = mtmd_context_params_default();
    vision_params.use_gpu = false;
    vision_params.n_threads = threads;
    vision_params.print_timings = false;
    vision_params.warmup = false;
    vision_params.image_max_tokens = 256;
    vision_params.cb_eval = graph_progress;
    vision_params.cb_eval_user_data = nullptr;

    next->vision = mtmd_init_from_file(projector_path.c_str(), next->model, vision_params);
    if (!next->vision || !mtmd_support_vision(next->vision)) throw std::runtime_error(is_cancelled() ? "Model loading cancelled" : "Cannot load matching local vision projector");
    if (is_cancelled()) throw std::runtime_error("Model loading cancelled");
    engine = std::move(next);
    loaded.store(true);
}

Result analyze(const std::string & image_path, const std::string & prompt, int max_tokens, std::uint64_t epoch) {
    std::lock_guard<std::mutex> lock(operation_mutex);
    active_epoch = epoch;
    Result result;
    if (is_cancelled()) { result.cancelled = true; return result; }
    if (!engine || !loaded.load()) throw std::runtime_error("Offline model is not loaded");
    if (prompt.empty() || prompt.size() > 2000 || prompt.find(mtmd_default_marker()) != std::string::npos) throw std::runtime_error("Invalid visual prompt");
    max_tokens = std::clamp(max_tokens, 1, 128);
    const auto start = Clock::now();
#if !defined(CYBER_VISUAL_FINDINGS)
    llama_memory_clear(llama_get_memory(engine->context), true);
#endif
    const auto bytes = read_image(image_path);
    auto wrapped = mtmd_helper_bitmap_init_from_buf(engine->vision, bytes.data(), bytes.size(), false);
    std::unique_ptr<mtmd_bitmap, decltype(&mtmd_bitmap_free)> bitmap(wrapped.bitmap, mtmd_bitmap_free);
    if (!bitmap || mtmd_bitmap_is_audio(bitmap.get()) || mtmd_bitmap_get_nx(bitmap.get()) > 1280 || mtmd_bitmap_get_ny(bitmap.get()) > 1280) throw std::runtime_error("Cannot decode normalized frame");
    const std::string formatted = format_prompt(prompt);
    mtmd_input_text input{formatted.c_str(), true, true};
    const mtmd_bitmap * bitmaps[] = {bitmap.get()};
    std::unique_ptr<mtmd_input_chunks, decltype(&mtmd_input_chunks_free)> chunks(mtmd_input_chunks_init(), mtmd_input_chunks_free);
    const int tokenized = mtmd_tokenize(engine->vision, chunks.get(), &input, bitmaps, 1);
    if (tokenized != 0) throw std::runtime_error("Cannot prepare image prompt");
#if defined(CYBER_VISUAL_FINDINGS)
    if (llama_model_n_embd_inp(engine->model) != visionpsy_findings::embedding_size) {
        throw std::runtime_error("VisionPsy image token size does not match classifier");
    }
    result.text = classify_frame(chunks.get());
    result.cancelled = is_cancelled();
    result.visual_findings = true;
    result.total_ms = milliseconds(start, Clock::now());
    result.prompt_ms = result.total_ms;
    return result;
#else
    result.input_tokens = static_cast<int>(mtmd_helper_get_n_tokens(chunks.get()));
    if (result.input_tokens + max_tokens + 8 >= CONTEXT_SIZE) throw std::runtime_error("Frame prompt exceeds local context");
    llama_pos n_past = 0;
    int status = mtmd_helper_eval_chunks(engine->vision, engine->context, chunks.get(), 0, 0, BATCH_SIZE, true, &n_past);
    auto generated_at = Clock::now();
    result.prompt_ms = milliseconds(start, generated_at);
    if (status != 0 && !is_cancelled()) throw std::runtime_error("Local image encoding failed");
    const auto * vocab = llama_model_get_vocab(engine->model);
    std::unique_ptr<llama_sampler, decltype(&llama_sampler_free)> sampler(llama_sampler_init_greedy(), llama_sampler_free);
    for (int i = 0; i < max_tokens && !is_cancelled(); ++i) {
        llama_token token = llama_sampler_sample(sampler.get(), engine->context, -1);
        if (llama_vocab_is_eog(vocab, token)) break;
        result.text += token_piece(vocab, token);
        result.generated_tokens += 1;
        if (i + 1 >= max_tokens) break;
        if (llama_decode(engine->context, llama_batch_get_one(&token, 1)) != 0) {
            if (!is_cancelled()) throw std::runtime_error("Local text generation failed");
            break;
        }
    }
    const auto finished = Clock::now();
    result.cancelled = is_cancelled();
    result.generate_ms = milliseconds(generated_at, finished);
    result.total_ms = milliseconds(start, finished);
    llama_memory_clear(llama_get_memory(engine->context), true);
    return result;
#endif
}

std::uint64_t begin_operation() { return cancel_epoch.load(); }
void cancel() { cancel_epoch.fetch_add(1); }
bool is_loaded() { return loaded.load(); }
void unload() {
    std::lock_guard<std::mutex> lock(operation_mutex);
    loaded.store(false);
    engine.reset();
}
}
