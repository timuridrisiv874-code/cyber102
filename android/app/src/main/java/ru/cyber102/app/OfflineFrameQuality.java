package ru.cyber102.app;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import java.io.IOException;

/** Cheap conservative rejection of blank/covered-lens frames before costly inference. */
final class OfflineFrameQuality {
    private OfflineFrameQuality() { }
    static void check(byte[] jpeg) throws IOException {
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inSampleSize = 8;
        Bitmap sample = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.length, options);
        if (sample == null) throw new IOException("Кадр не удалось прочитать. Выберите другое фото.");
        try {
            double sum = 0, squares = 0;
            int count = sample.getWidth() * sample.getHeight();
            for (int y = 0; y < sample.getHeight(); y++) for (int x = 0; x < sample.getWidth(); x++) {
                int rgb = sample.getPixel(x, y);
                double value = .299 * ((rgb >> 16) & 255) + .587 * ((rgb >> 8) & 255) + .114 * (rgb & 255);
                sum += value; squares += value * value;
            }
            double mean = sum / count;
            if (squares / count - mean * mean < 4) {
                throw new IOException("Кадр почти однотонный: оборудование не различимо. Проверьте объектив, освещение и наведите камеру на весь узел.");
            }
        } finally { sample.recycle(); }
    }
}
