package ru.cyber102.app;

/** CPU-only local inference. There are no network calls in the native engine. */
public final class SmolNative {
    private static volatile boolean available;
    private static boolean attempted;

    private SmolNative() { }

    /** Called only by the serial model executor. Status reads never load JNI. */
    static boolean initialize() {
        if (attempted) return available;
        android.os.Trace.beginSection("Cyber102.smol.link");
        try {
            if (!supportsDotProduct()) return false;
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

    private static boolean supportsDotProduct() {
        // The Redmi build uses ARMv8.2 dot-product kernels. Check every listed
        // core before dlopen. This read happens on the model worker.
        try (java.io.BufferedReader cpu = new java.io.BufferedReader(new java.io.FileReader("/proc/cpuinfo"))) {
            boolean found = false;
            for (String line; (line = cpu.readLine()) != null;) {
                if (!line.startsWith("Features")) continue;
                found = true;
                if (!java.util.Arrays.asList(line.trim().split("\\s+")).contains("asimddp")) return false;
            }
            return found;
        } catch (java.io.IOException | SecurityException error) {
            return false;
        }
    }

    public static boolean isAvailable() { return available; }
    public static native long beginOperation();
    public static native void load(String modelPath, String projectorPath, int threads, long epoch);
    public static native String analyze(String imagePath, String prompt, int maxTokens, long epoch);
    public static native void cancel();
    public static native void unload();
    public static native boolean isLoaded();
}
