package pzformat;
import java.nio.file.*;
import java.util.*;

/** Cross-language oracle for CellData: load a cell pair, edit, write, compare. */
public final class CellOracle {
    public static void main(String[] args) throws Exception {
        String cmd = args[0];
        Path pack = Path.of(args[1]);
        Path hdr  = Path.of(args[2]);
        if (cmd.equals("loadwrite")) {
            CellData c = CellData.load(pack, hdr);
            byte[] out = c.writeLotPack();
            byte[] in  = Files.readAllBytes(pack);
            boolean same = Arrays.equals(in, out);
            System.out.println("java loadwrite: cellSize=" + c.cellSize
                + " levels=" + c.levelCount + " minLevel=" + c.minLevel
                + " nonEmpty=" + c.nonEmptySquares()
                + " round-trip " + (same ? "IDENTICAL" : "DIFFERS"));
            if (!same) System.exit(1);
            // Edit: fill a 4x4 at maxLevel-anchored z=0, then re-write to a file
            // for the C++ side to load and confirm the same edit.
            c.fill("oracle_fill_01_0", 1, 1, 4, 4, 0);
            Files.write(Path.of(args[3]), c.writeLotPack());
            Files.write(Path.of(args[4]), c.writeLotHeader());
            System.out.println("java loadwrite: wrote edited cell, nonEmpty now " + c.nonEmptySquares());
        } else if (cmd.equals("checkedit")) {
            CellData c = CellData.load(pack, hdr);
            // Confirm the 4x4 fill at z=0 is present with the expected name.
            int zi = c.zIndex(0);
            String[] n = c.tileNamesAt(3, 3, 0);
            boolean ok = n != null && n.length == 1 && n[0].equals("oracle_fill_01_0");
            System.out.println("java checkedit: fill present=" + ok
                + " nonEmpty=" + c.nonEmptySquares());
            if (!ok) System.exit(1);
        }
    }
}
