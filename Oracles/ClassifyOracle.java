package pzformat;
import java.nio.file.*;
import java.util.*;

/** Emit a classification digest for every tile, sorted, for cross-language diff. */
public final class ClassifyOracle {
    public static void main(String[] args) throws Exception {
        TileIndex ti = TileIndex.load(Path.of(args[1]));
        List<String> lines = new ArrayList<>();
        for (String name : ti.byName.keySet()) {
            String kind = ti.kindOf(name).name();
            String edge = ti.edgeOf(name).name();
            String dec  = ti.decorationEdge(name).name();
            int flags = (ti.isStructuralWall(name)?1:0)
                      | (ti.isWallFixture(name)?2:0)
                      | (ti.isDoorway(name)?4:0)
                      | (ti.isWindowWall(name)?8:0)
                      | (ti.isOverlay(name)?16:0)
                      | (ti.blocksMovement(name)?32:0);
            String ct = ti.containerType(name); if (ct==null) ct="-";
            lines.add(name+"\t"+kind+"\t"+edge+"\t"+dec+"\t"+flags+"\t"+ct);
        }
        Collections.sort(lines);
        Path out = Path.of(args[2]);
        Files.write(out, lines);
        System.out.println("java classify: "+lines.size()+" tiles, files="+ti.fileCount
            +" tilesets="+ti.tilesetCount+" -> "+out);
    }
}
