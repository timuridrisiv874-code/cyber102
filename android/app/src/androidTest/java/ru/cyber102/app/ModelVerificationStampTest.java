package ru.cyber102.app;

import static org.junit.Assert.*;
import android.os.Build;
import androidx.test.platform.app.InstrumentationRegistry;
import java.io.File;
import java.io.FileInputStream;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.FileTime;
import org.junit.Assume;
import org.junit.Test;

public final class ModelVerificationStampTest {
    @Test public void reuseOnlyTheVerifiedFileAndManifest() throws Exception {
        File directory = Files.createTempDirectory(
                InstrumentationRegistry.getInstrumentation().getTargetContext().getNoBackupFilesDir().toPath(), "lfm-test-").toFile();
        try {
            File weights = new File(directory, "model.gguf");
            Files.write(weights.toPath(), new byte[]{1, 2, 3});
            FileTime timestamp = FileTime.fromMillis(1_600_000_000_123L);
            Files.setLastModifiedTime(weights.toPath(), timestamp);
            ModelVerificationStamp stamp = new ModelVerificationStamp(directory, "model", "expected");
            String original = stamp.identity(weights);
            if (Build.VERSION.SDK_INT < 27) {
                assertNull(original);
                assertFalse(stamp.matches(original));
                return;
            }
            Assume.assumeNotNull(original);
            try (FileInputStream stream = new FileInputStream(weights)) {
                assertEquals(original, stamp.identity(stream.getFD()));
            }
            assertFalse(stamp.matches(original));
            stamp.record(original);
            assertTrue(new ModelVerificationStamp(directory, "model", "expected").matches(original));
            ModelVerificationStamp newManifest = new ModelVerificationStamp(directory, "model", "different");
            assertFalse(newManifest.matches(newManifest.identity(weights)));

            // Preserve inode, byte length and mtime. Only ctime records this edit.
            android.os.SystemClock.sleep(80);
            Files.write(weights.toPath(), new byte[]{9, 2, 3});
            Files.setLastModifiedTime(weights.toPath(), timestamp);
            assertFalse(stamp.matches(stamp.identity(weights)));
            stamp.record(stamp.identity(weights));
            File replacement = new File(directory, "replacement");
            Files.write(replacement.toPath(), new byte[]{9, 2, 3});
            Files.setLastModifiedTime(replacement.toPath(), timestamp);
            Files.move(replacement.toPath(), weights.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            assertFalse(stamp.matches(stamp.identity(weights)));
            assertFalse(stamp.matches(stamp.identity(replacement)));
        } finally {
            File[] files = directory.listFiles();
            if (files != null) for (File file : files) Files.deleteIfExists(file.toPath());
            Files.deleteIfExists(directory.toPath());
        }
    }
}
