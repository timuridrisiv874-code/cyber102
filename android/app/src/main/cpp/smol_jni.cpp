#include "smol_engine.h"
#include <jni.h>
#include <stdexcept>
#include <string>

namespace {
std::string utf8(JNIEnv * env, jstring value) {
    if (!value) throw std::runtime_error("Missing native argument");
    const char * bytes = env->GetStringUTFChars(value, nullptr);
    if (!bytes) throw std::runtime_error("Cannot read native argument");
    std::string result(bytes);
    env->ReleaseStringUTFChars(value, bytes);
    return result;
}
void fail(JNIEnv * env, const std::exception & error) {
    env->ThrowNew(env->FindClass("java/lang/IllegalStateException"), error.what());
}
jstring java_utf8(JNIEnv * env, const std::string & text) {
    jbyteArray bytes = env->NewByteArray(static_cast<jsize>(text.size()));
    if (!bytes) return nullptr;
    env->SetByteArrayRegion(bytes, 0, static_cast<jsize>(text.size()), reinterpret_cast<const jbyte *>(text.data()));
    jclass string_class = env->FindClass("java/lang/String");
    jmethodID constructor = env->GetMethodID(string_class, "<init>", "([BLjava/lang/String;)V");
    jstring charset = env->NewStringUTF("UTF-8");
    jstring result = static_cast<jstring>(env->NewObject(string_class, constructor, bytes, charset));
    env->DeleteLocalRef(charset);
    env->DeleteLocalRef(bytes);
    env->DeleteLocalRef(string_class);
    return result;
}
}

extern "C" JNIEXPORT void JNICALL Java_ru_cyber102_app_SmolNative_load(JNIEnv * env, jclass, jstring model, jstring projector, jint threads, jlong epoch) {
    try { cyber_smol::load(utf8(env, model), utf8(env, projector), threads, static_cast<std::uint64_t>(epoch)); }
    catch (const std::exception & error) { fail(env, error); }
}
extern "C" JNIEXPORT jstring JNICALL Java_ru_cyber102_app_SmolNative_analyze(JNIEnv * env, jclass, jstring image, jstring prompt, jint tokens, jlong epoch) {
    try { return java_utf8(env, cyber_smol::analyze(utf8(env, image), utf8(env, prompt), tokens, static_cast<std::uint64_t>(epoch)).json()); }
    catch (const std::exception & error) { fail(env, error); return nullptr; }
}
extern "C" JNIEXPORT jlong JNICALL Java_ru_cyber102_app_SmolNative_beginOperation(JNIEnv *, jclass) {
    return static_cast<jlong>(cyber_smol::begin_operation());
}
extern "C" JNIEXPORT void JNICALL Java_ru_cyber102_app_SmolNative_cancel(JNIEnv *, jclass) { cyber_smol::cancel(); }
extern "C" JNIEXPORT void JNICALL Java_ru_cyber102_app_SmolNative_unload(JNIEnv * env, jclass) {
    try { cyber_smol::unload(); } catch (const std::exception & error) { fail(env, error); }
}
extern "C" JNIEXPORT jboolean JNICALL Java_ru_cyber102_app_SmolNative_isLoaded(JNIEnv *, jclass) { return cyber_smol::is_loaded() ? JNI_TRUE : JNI_FALSE; }
