#include "CyberInspection.h"
#include "smol_engine.h"
#include <cstdlib>
#include <cstring>
#include <exception>
#include <stdexcept>
#include <string>

namespace {
// Still return a valid error if allocating an output string fails.
char memory_error[] = "Insufficient memory for inspection";
char memory_error_json[] = "{\"error\":\"Insufficient memory for inspection\"}";

char *owned(const char *source, char *fallback) noexcept {
    char *copy = ::strdup(source);
    return copy ? copy : fallback;
}

const char *required(const char *value) {
    if (!value || !*value) throw std::invalid_argument("Missing local file or inspection prompt");
    return value;
}

char *json_error(const char *message) noexcept {
    try {
        std::string out = "{\"error\":\"";
        constexpr char hex[] = "0123456789abcdef";
        for (const unsigned char *p = reinterpret_cast<const unsigned char *>(message); *p; ++p) {
            if (*p == '"' || *p == '\\') out += '\\';
            if (*p < 32) {
                out += "\\u00";
                out += hex[*p >> 4];
                out += hex[*p & 15];
            } else out += static_cast<char>(*p);
        }
        out += "\"}";
        return owned(out.c_str(), memory_error_json);
    } catch (...) {
        return memory_error_json;
    }
}
}

uint64_t cyber_lfm_begin() { return cyber_smol::begin_operation(); }

char *cyber_lfm_load(const char *model, const char *projector, int32_t threads, uint64_t epoch) {
    try {
        cyber_smol::load(required(model), required(projector), threads, epoch);
        return nullptr;
    } catch (const std::exception &error) {
        return owned(error.what(), memory_error);
    } catch (...) {
        return owned("Model loading failed", memory_error);
    }
}

char *cyber_lfm_analyze(const char *jpeg, const char *prompt, int32_t max_tokens, uint64_t epoch) {
    try {
        auto result = cyber_smol::analyze(required(jpeg), required(prompt), max_tokens, epoch);
        return owned(result.json().c_str(), memory_error_json);
    } catch (const std::exception &error) {
        return json_error(error.what());
    } catch (...) {
        return json_error("Inspection failed");
    }
}

void cyber_lfm_free(char *result) {
    if (result != memory_error && result != memory_error_json) std::free(result);
}
void cyber_lfm_cancel() { cyber_smol::cancel(); }
void cyber_lfm_unload() { cyber_smol::unload(); }
bool cyber_lfm_loaded() { return cyber_smol::is_loaded(); }
