package ru.cyber102.app;

/** CPU-only local inference. There are no network calls in the native engine. */
public final class SmolNative {
    private static final boolean AVAILABLE;
    static {
        boolean available;
        try { System.loadLibrary("cyber_smol"); available = true; }
        catch (UnsatisfiedLinkError | SecurityException error) { available = false; }
        AVAILABLE = available;
    }
    private SmolNative() { }
    public static boolean isAvailable() { return AVAILABLE; }
    public static native long beginOperation();
    public static native void load(String modelPath, String projectorPath, int threads, long epoch);
    public static native String analyze(String imagePath, String prompt, int maxTokens, long epoch);
    public static native void cancel();
    public static native void unload();
    public static native boolean isLoaded();
}
