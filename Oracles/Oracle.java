package pzformat;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;

/**
 * Cross-language oracle, Java side.
 *
 * emit  <path>   build a synthetic B42 lotheader with the Java writer, save it
 * check <path>   read a header the C++ wrote, re-write it with the Java writer,
 *                and report whether the bytes match
 * pack  <path>   read a .lotpack the C++ wrote, re-encode every chunk with the
 *                Java encoder, and report whether the bytes match
 *
 * This is the comparison against an independent source that Charter §4 asks
 * for. The two trees share no code; agreement is evidence.
 */
public final class Oracle {

    /** Deliberately awkward: high bytes, empty names, negative minLevel, empty building. */
    static LotHeader synth() {
        LotHeader h = new LotHeader();
        h.b42 = true;
        h.version = 1;
        h.tileNames.add("floors_exterior_natural_01_0");
        h.tileNames.add("walls_interior_house_01_11");
        h.tileNames.add("blends_natural_01_64");
        h.tileNames.add("tile\u0080\u00FF_high_bytes");
        h.tileNames.add("");

        h.levelsAbove = 8;
        h.levelsBelow = 8;
        h.minLevel = -2;      // the ~70 cells with basements
        h.unknown12 = 7;

        LotHeader.Room r0 = new LotHeader.Room();
        r0.name = "livingroom";
        r0.floor = 0;
        r0.rects.add(new int[]{10, 12, 6, 5});
        r0.rects.add(new int[]{16, 12, 3, 2});
        r0.objects.add(new int[]{1, 10, 12});
        r0.objects.add(new int[]{-1, 0, 0});
        h.rooms.add(r0);

        LotHeader.Room r1 = new LotHeader.Room();
        r1.name = "bathroom";
        r1.floor = 1;
        r1.rects.add(new int[]{20, 20, 2, 3});
        h.rooms.add(r1);

        LotHeader.Room r2 = new LotHeader.Room();
        r2.name = "";          // empty room name: nlString writes just the \n
        r2.floor = -2;
        h.rooms.add(r2);

        h.buildings.add(new int[]{0, 1});
        h.buildings.add(new int[]{2});
        h.buildings.add(new int[]{});   // zero-room building

        h.chunkGrid = new byte[LotHeader.GRID_BYTES];
        for (int i = 0; i < LotHeader.GRID_BYTES; i++) h.chunkGrid[i] = (byte) (i * 7 + 3);
        return h;
    }

    public static void main(String[] args) throws Exception {
        String cmd = args[0];
        Path p = Path.of(args[1]);

        switch (cmd) {
            case "emit" -> {
                byte[] bytes = synth().write();
                Files.write(p, bytes);
                // Prove the Java reader agrees with the Java writer first.
                LotHeader back = LotHeader.read(new LE(bytes));
                byte[] again = back.write();
                System.out.println("java emit: " + bytes.length + " bytes, "
                        + "java self round-trip " + (Arrays.equals(bytes, again) ? "OK" : "MISMATCH")
                        + ", fullyConsumed=" + back.fullyConsumed
                        + ", tiles=" + back.tileNames.size()
                        + ", rooms=" + back.rooms.size()
                        + ", buildings=" + back.buildings.size()
                        + ", roomRefs=" + back.roomRefs()
                        + ", minLevel=" + back.minLevel
                        + ", levelCount=" + back.levelCount());
            }
            case "check" -> {
                byte[] fromCpp = Files.readAllBytes(p);
                LotHeader h = LotHeader.read(new LE(fromCpp));
                byte[] javaRewrite = h.write();
                boolean same = Arrays.equals(fromCpp, javaRewrite);
                System.out.println("java check: read C++ output, "
                        + fromCpp.length + " bytes, re-write "
                        + (same ? "IDENTICAL" : "DIFFERS")
                        + ", tiles=" + h.tileNames.size()
                        + ", rooms=" + h.rooms.size()
                        + ", buildings=" + h.buildings.size()
                        + ", minLevel=" + h.minLevel
                        + ", unknown12=" + h.unknown12
                        + ", fullyConsumed=" + h.fullyConsumed);
                if (!same) {
                    System.out.println("  first diff at " + firstDiff(fromCpp, javaRewrite));
                    System.exit(1);
                }
            }
            case "pack" -> {
                LotHeader h = new LotHeader();
                h.b42 = true;
                h.minLevel = 0;
                h.unknown12 = 7;
                LotPack lp = LotPack.read(p, h);
                boolean allSame = true;
                for (int i = 0; i < lp.chunkCount; i++) {
                    byte[] original = lp.rawChunk(i);
                    byte[] again = lp.encodeChunk(lp.chunk(i / lp.chunksPerSide,
                                                            i % lp.chunksPerSide),
                                                  LotPack.Policy.SPAN_LEVELS_MINIMAL);
                    if (!Arrays.equals(original, again)) {
                        allSame = false;
                        System.out.println("  chunk " + i + " differs: "
                                + original.length + " vs " + again.length);
                    }
                }
                byte[] whole = lp.write(LotPack.Policy.SPAN_LEVELS_MINIMAL);
                boolean fileSame = Arrays.equals(lp.rawFile(), whole);
                System.out.println("java pack: read C++ lotpack, chunkCount=" + lp.chunkCount
                        + ", perSide=" + lp.chunksPerSide
                        + ", cellSize=" + lp.cellSize
                        + ", per-chunk re-encode " + (allSame ? "IDENTICAL" : "DIFFERS")
                        + ", whole-file rewrite " + (fileSame ? "IDENTICAL" : "DIFFERS"));
                if (!allSame || !fileSame) System.exit(1);
            }
            case "sweep" -> RoundTrip.run(p, 0);
            default -> throw new IllegalArgumentException("unknown command " + cmd);
        }
    }

    static String firstDiff(byte[] a, byte[] b) {
        int n = Math.min(a.length, b.length);
        for (int i = 0; i < n; i++) {
            if (a[i] != b[i]) {
                return "offset " + i + ": " + String.format("%02X", a[i])
                        + " vs " + String.format("%02X", b[i]);
            }
        }
        return "length " + a.length + " vs " + b.length;
    }
}
