package pzformat;

import java.nio.file.*;
import java.util.*;

/** Cross-language oracle for TileBin. Emits a synthetic tdef, or checks one
 *  the C++ side wrote, parsing with the confirmed retail layout. */
public final class TileOracle {

    static byte[] synthTiles() {
        LEW w = new LEW();
        w.ascii("tdef");
        w.i32(1);
        w.i32(1);
        w.nlString("advertising_01");
        w.nlString("advertising_01.png");
        w.i32(8); w.i32(16); w.i32(88);
        w.i32(2);
        w.i32(2);
        w.nlString("Facing"); w.nlString("S");
        w.nlString("solid");  w.nlString("");
        w.i32(3);
        w.nlString("Facing"); w.nlString("W");
        w.nlString("container"); w.nlString("crate");
        w.nlString("ContainerCapacity"); w.nlString("10");
        return w.toByteArray();
    }

    public static void main(String[] args) throws Exception {
        String cmd = args[0];
        Path p = Path.of(args[1]);
        if (cmd.equals("emit")) {
            byte[] bytes = synthTiles();
            Files.write(p, bytes);
            TileBin tb = TileBin.read(bytes, TileBin.TileShape.COUNT_ONLY, 0);
            System.out.println("java emit: " + bytes.length + " bytes, "
                + tb.tilesetCount + " tilesets, " + tb.byName.size() + " tiles"
                + ", adv_0 solid=" + tb.byName.get("advertising_01_0").solid()
                + ", adv_1 container=" + tb.byName.get("advertising_01_1").get("container"));
        } else if (cmd.equals("check")) {
            byte[] bytes = Files.readAllBytes(p);
            TileBin tb = TileBin.read(bytes, TileBin.TileShape.COUNT_ONLY, 0);
            boolean ok = tb.byName.size() == 2
                && tb.byName.get("advertising_01_0").solid()
                && tb.byName.get("advertising_01_0").facing().equals("S")
                && tb.byName.get("advertising_01_1").get("container").equals("crate")
                && tb.byName.get("advertising_01_1").get("ContainerCapacity").equals("10");
            System.out.println("java check: read C++ tdef, " + bytes.length + " bytes, "
                + tb.byName.size() + " tiles, content " + (ok ? "MATCHES" : "DIFFERS"));
            if (!ok) System.exit(1);
        }
    }
}
