#include <jni.h>
#include <android/asset_manager.h>
#include <android/asset_manager_jni.h>
#include <ctranslate2/translator.h>
#include <sentencepiece_processor.h>
#include <algorithm>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
constexpr size_t MAX_SOURCE_PIECES = 120;

std::string read_asset(AAssetManager* manager, const std::string& filename) {
    std::string path = "en-ru/" + filename;
    std::unique_ptr<AAsset, decltype(&AAsset_close)> asset(
        AAssetManager_open(manager, path.c_str(), AASSET_MODE_STREAMING), AAsset_close);
    if (!asset) throw std::runtime_error("Missing translation asset: " + filename);
    const auto length = AAsset_getLength64(asset.get());
    if (length <= 0 || length > 256 * 1024 * 1024) throw std::runtime_error("Invalid translation asset size");
    std::string buffer(static_cast<size_t>(length), '\0');
    size_t offset = 0;
    while (offset < buffer.size()) {
        const int n = AAsset_read(asset.get(), buffer.data() + offset, buffer.size() - offset);
        if (n <= 0) throw std::runtime_error("Incomplete translation asset: " + filename);
        offset += static_cast<size_t>(n);
    }
    return buffer;
}

void require_ok(const sentencepiece::util::Status& status) {
    if (!status.ok()) throw std::runtime_error(status.ToString());
}

class LocalTranslator {
public:
    explicit LocalTranslator(AAssetManager* assets) {
        require_ok(tokenizer.LoadFromSerializedProto(read_asset(assets, "sentencepiece.model")));
        auto files = std::make_shared<ctranslate2::models::ModelMemoryReader>("opus-tiny-eng-rus-int8");
        for (const char* filename : {"config.json", "shared_vocabulary.json", "model.bin"})
            files->register_file(filename, read_asset(assets, filename));
        ctranslate2::models::ModelLoader loader(files);
        loader.device = ctranslate2::Device::CPU;
        loader.compute_type = ctranslate2::ComputeType::INT8_FLOAT32;
        ctranslate2::ReplicaPoolConfig config;
        config.num_threads_per_replica = 4;
        translator = std::make_unique<ctranslate2::Translator>(loader, config);
    }

    std::string translate(const std::string& text) {
        std::vector<std::string> pieces;
        require_ok(tokenizer.Encode(text, &pieces));
        if (pieces.empty()) return {};
        std::vector<std::vector<std::string>> batches;
        for (size_t i = 0; i < pieces.size(); i += MAX_SOURCE_PIECES) {
            const size_t end = std::min(i + MAX_SOURCE_PIECES, pieces.size());
            batches.emplace_back(pieces.begin() + i, pieces.begin() + end);
        }
        ctranslate2::TranslationOptions options;
        options.beam_size = 4;
        options.max_input_length = 0;
        options.max_decoding_length = 256;
        options.return_end_token = true;
        auto results = translator->translate_batch(batches, options, 8);
        std::string translation;
        for (auto& result : results) {
            auto& words = result.hypotheses.at(0);
            if (words.empty() || (words.back() != "</s>" && words.size() >= options.max_decoding_length))
                throw std::runtime_error("Translation exceeded output limit");
            words.erase(std::remove_if(words.begin(), words.end(), [](const std::string& s) {
                return s == "</s>" || s == "<s>" || s == "<pad>";
            }), words.end());
            std::string sentence;
            require_ok(tokenizer.Decode(words, &sentence));
            if (sentence.empty()) throw std::runtime_error("Empty translation");
            if (!translation.empty()) translation.push_back(' ');
            translation += sentence;
        }
        return translation;
    }
private:
    sentencepiece::SentencePieceProcessor tokenizer;
    std::unique_ptr<ctranslate2::Translator> translator;
};

void throw_io(JNIEnv* env, const std::exception& error) {
    env->ThrowNew(env->FindClass("java/io/IOException"), error.what());
}
}

extern "C" JNIEXPORT jlong JNICALL
Java_ru_cyber102_app_OfflineTranslator_nativeOpen(JNIEnv* env, jclass, jobject assets) {
    try {
        auto manager = AAssetManager_fromJava(env, assets);
        if (!manager) throw std::runtime_error("Cannot access embedded translator assets");
        return reinterpret_cast<jlong>(new LocalTranslator(manager));
    } catch (const std::exception& error) { throw_io(env, error); return 0; }
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_ru_cyber102_app_OfflineTranslator_nativeTranslate(JNIEnv* env, jclass, jlong handle, jbyteArray source) {
    try {
        if (!handle || !source) throw std::runtime_error("Translator is not ready");
        const jsize len = env->GetArrayLength(source);
        std::string sentence(static_cast<size_t>(len), '\0');
        env->GetByteArrayRegion(source, 0, len, reinterpret_cast<jbyte*>(sentence.data()));
        if (env->ExceptionCheck()) return nullptr;
        std::string output = reinterpret_cast<LocalTranslator*>(handle)->translate(sentence);
        jbyteArray bytes = env->NewByteArray(static_cast<jsize>(output.size()));
        if (bytes) env->SetByteArrayRegion(bytes, 0, static_cast<jsize>(output.size()),
                                           reinterpret_cast<const jbyte*>(output.data()));
        return bytes;
    } catch (const std::exception& error) { throw_io(env, error); return nullptr; }
}

extern "C" JNIEXPORT void JNICALL
Java_ru_cyber102_app_OfflineTranslator_nativeClose(JNIEnv*, jclass, jlong handle) {
    delete reinterpret_cast<LocalTranslator*>(handle);
}
