package ru.cyber102.app;

import android.os.Build;
import android.system.ErrnoException;
import android.system.Os;
import android.system.OsConstants;
import android.system.StructStat;
import android.util.AtomicFile;
import java.io.File;
import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

/** A private receipt for an already verified inode. A missing or unusable receipt is a cache miss. */
final class ModelVerificationStamp {
    private final AtomicFile receipt;
    private final String expectedHash;

    ModelVerificationStamp(File noBackupDirectory, String role, String expectedHash) {
        receipt = new AtomicFile(new File(noBackupDirectory, "lfm3-" + role + ".verified"));
        this.expectedHash = expectedHash;
    }

    String identity(File file) {
        try {
            return identity(Os.lstat(file.getPath()));
        } catch (ErrnoException ignored) {
            return null;
        }
    }

    String identity(FileDescriptor descriptor) {
        try {
            return identity(Os.fstat(descriptor));
        } catch (ErrnoException ignored) {
            return null;
        }
    }

    private String identity(StructStat stat) {
        // API 26 only exposes whole-second ctime. Also skip apparently coarse filesystem change times.
        if (Build.VERSION.SDK_INT < 27 || !OsConstants.S_ISREG(stat.st_mode) || stat.st_ctim.tv_nsec == 0) return null;
        return "1:" + expectedHash + ":" + stat.st_dev + ":" + stat.st_ino + ":" + stat.st_size
                + ":" + stat.st_mtim.tv_sec + ":" + stat.st_mtim.tv_nsec
                + ":" + stat.st_ctim.tv_sec + ":" + stat.st_ctim.tv_nsec;
    }

    boolean matches(String identity) {
        if (identity == null) return false;
        byte[] expected = identity.getBytes(StandardCharsets.US_ASCII);
        try {
            return receipt.getBaseFile().length() == expected.length && Arrays.equals(receipt.readFully(), expected);
        } catch (IOException ignored) {
            return false;
        }
    }

    void record(String verifiedIdentity) {
        if (verifiedIdentity == null) return;
        FileOutputStream output = null;
        try {
            output = receipt.startWrite();
            output.write(verifiedIdentity.getBytes(StandardCharsets.US_ASCII));
            receipt.finishWrite(output);
        } catch (IOException ignored) {
            if (output != null) receipt.failWrite(output);
        }
    }
}
