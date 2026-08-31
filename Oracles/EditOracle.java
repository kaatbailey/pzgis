package pzformat;
import java.nio.file.*;
import java.util.*;

/** Cross-language oracle for CellEditor. Uses an EMPTY TileIndex so it runs
 *  without real .tiles: the script exercises journal, grouping, undo, redo and
 *  the classification-independent ops (fill via setFloor prepend, addObject,
 *  setRoom, clearSquare). Both trees must produce identical final bytes. */
public final class EditOracle {
    public static void main(String[] args) throws Exception {
        TileIndex ti = new TileIndex(); // empty: kindOf==UNKNOWN, no walls/floors classified
        List<String> names = new ArrayList<>(Arrays.asList(
            "t0","t1","t2","t3"));
        LotHeader h = CellData.newHeader(names, 0, 7);
        CellData c = CellData.blank(h, 4);
        CellEditor ed = new CellEditor(c, ti);

        // With an empty index, setFloor always prepends (no floor recognized),
        // which is deterministic and identical across trees.
        ed.begin("script");
        ed.setFloor(2,2,0,"t0");
        ed.setFloor(2,2,0,"t1");   // no floor recognized -> prepends again
        ed.addObject(2,2,0,"t2");
        ed.addObject(3,3,0,"t3");
        ed.setRoom(3,3,0,9);
        ed.end();
        ed.clearSquare(3,3,0);
        ed.undo();                 // bring 3,3 back
        ed.undo();                 // undo the whole grouped script
        ed.redo();                 // redo the script

        Files.write(Path.of(args[0]), c.writeLotPack());
        Files.write(Path.of(args[1]), c.writeLotHeader());
        System.out.println("java edit: undoDepth="+ed.undoDepth()
            +" canRedo="+ed.canRedo()+" nonEmpty="+c.nonEmptySquares());
    }
}
