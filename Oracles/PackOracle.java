package pzformat;
import java.nio.file.*;
import java.util.*;

/** Cross-language oracle for PackFile + SpriteNames. */
public final class PackOracle {
    public static void main(String[] args) throws Exception {
        String cmd = args[0];
        if (cmd.equals("check")) {
            // read a pack the C++ side wrote, re-write it, compare
            byte[] fromCpp = Files.readAllBytes(Path.of(args[1]));
            PackFile pf = PackFile.read(new LE(fromCpp));
            byte[] rw = pf.write();
            boolean same = Arrays.equals(fromCpp, rw);
            int entries = 0; for (PackFile.Page p : pf.pages) entries += p.entries.size();
            System.out.println("java check: read C++ pack, " + fromCpp.length + " bytes, "
                + pf.pages.size() + " pages, " + entries + " entries, pzpk=" + pf.pzpk
                + ", re-write " + (same ? "IDENTICAL" : "DIFFERS"));
            if (!same) System.exit(1);
        } else if (cmd.equals("spritecount")) {
            Set<String> names = SpriteNames.load(Path.of(args[1]));
            System.out.println("java spritecount: " + names.size()
                + ", vegetation_trees_01_0 present=" + names.contains("vegetation_trees_01_0"));
        }
    }
}
