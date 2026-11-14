package ru.cyber102.app;

/** CPU-only local inference. There are no network calls in the native engine. */
public final class SmolNative {
    private static volatile boolean available;
    private static boolean attempted;

    private SmolNative() { }

    /** Called only by the serial model executor. Status reads never load JNI. */
    static boolean initialize() {
        if (attempted) return available;
        android.os.Trace.beginSection("Cyber102.lfm.link");
        try {
            System.loadLibrary("cyber_smol");
            available = true;
        } catch (UnsatisfiedLinkError | SecurityException error) {
            android.util.Log.w("Cyber102", "Unable to load offline inference library", error);
        } finally {
            attempted = true;
            android.os.Trace.endSection();
        }
        return available;
    }

    public static boolean isAvailable() { return available; }
    public static native long beginOperation();
    public static native void load(String modelPath, String projectorPath, int threads, long epoch);
    public static native String analyze(String imagePath, String prompt, int maxTokens, long epoch);
    public static native void cancel();
    public static native void unload();
    public static native boolean isLoaded();
}
