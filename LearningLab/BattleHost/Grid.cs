using UnityEngine;

namespace BattleHost;

public sealed class BattleGrid
{
    const int Path = 0, Candidate = 1, Solid = 2;
    const float HalfCell = 0.5f;
    static readonly (int X, int Y)[] Neighbors = [(0, 1), (1, 0), (0, -1), (-1, 0)];
    readonly bool[] walkable;
    readonly Dictionary<int, int[]> distances = [];
    readonly Dictionary<int, int[]> footprints = [];
    public int Width { get; }
    public int Height { get; }
    public HashSet<int> BaseCells { get; } = [];
    public BattleGrid(StageData stage)
    {
        Width = stage.Width; Height = stage.Height;
        if (Width <= 0 || Height <= 0 || stage.Terrain.Length != Width * Height)
            throw new ArgumentException("Invalid stage dimensions");
        var rng = new Random(stage.GenerationSeed);
        walkable = stage.Terrain.Select(t => t switch {
            Path => true, Candidate => rng.NextDouble() < stage.WallCandidatePathRate,
            Solid => false, _ => throw new ArgumentException("Unsupported terrain")
        }).ToArray();
        foreach (var b in stage.Bases)
        {
            RequireCell(b.Cell);
            walkable[Index(b.Cell)] = true; // same origin override as StageDefinitionCompiler
            foreach (var cell in b.Footprint)
            {
                RequireCell(cell);
                if (!BaseCells.Add(Index(cell))) throw new ArgumentException("Overlapping base footprints");
                footprints.Add(Index(cell), b.Footprint.Select(Index).ToArray());
            }
        }
    }
    public void RequireCell(int[] cell)
    {
        if (cell == null || cell.Length != 2 || cell[0] < 0 || cell[0] >= Width ||
            cell[1] < 0 || cell[1] >= Height) throw new ArgumentException("Cell outside stage");
    }
    public int Index(int[] c) => c[1] * Width + c[0];
    public int Index(Vector3 position)
    {
        int x = (int)MathF.Round(position.x / GameConfig.cellSize);
        int y = (int)MathF.Round(position.z / GameConfig.cellSize);
        return x >= 0 && x < Width && y >= 0 && y < Height ? y * Width + x : -1;
    }
    public int[] Cell(int index) => [index % Width, index / Width];
    public Vector3 World(int index) => GridMath.CellToWorld(new(index % Width, index / Width));
    public bool Walkable(int index) => index >= 0 && index < walkable.Length &&
        walkable[index] && !BaseCells.Contains(index);
    public IEnumerable<int> Adjacent(int index)
    {
        int x = index % Width, y = index / Width;
        foreach (var (dx, dy) in Neighbors)
        {
            int nx = x + dx, ny = y + dy;
            if (nx >= 0 && nx < Width && ny >= 0 && ny < Height) yield return ny * Width + nx;
        }
    }
    public int[] Distances(int goal)
    {
        if (distances.TryGetValue(goal, out var cached)) return cached;
        var result = Enumerable.Repeat(-1, Width * Height).ToArray();
        var queue = new Queue<int>();
        if (Walkable(goal)) { result[goal] = 0; queue.Enqueue(goal); }
        else if (BaseCells.Contains(goal))
        {
            var footprint = footprints[goal];
            foreach (int edge in footprint.SelectMany(Adjacent).Distinct())
                if (Walkable(edge)) { result[edge] = 0; queue.Enqueue(edge); }
        }
        while (queue.Count > 0)
        {
            int current = queue.Dequeue();
            foreach (int next in Adjacent(current))
                if (Walkable(next) && result[next] < 0)
                { result[next] = result[current] + 1; queue.Enqueue(next); }
        }
        distances[goal] = result;
        return result;
    }
    public int Next(int from, int goal)
    {
        if (!Walkable(from)) return from;
        var d = Distances(goal);
        if (d[from] <= 0) return from;
        return Adjacent(from).FirstOrDefault(c => d[c] >= 0 && d[c] < d[from], from);
    }
    public bool Reachable(int from, int goal) =>
        Walkable(from) && goal >= 0 && goal < walkable.Length && Distances(goal)[from] >= 0;
    public bool ClearSegment(Vector3 from, Vector3 to, bool sight = false, int targetBaseCell = -1)
    {
        float length = (to - from).magnitude;
        int probes = Math.Max(1, (int)MathF.Ceiling(length / (GameConfig.cellSize * HalfCell)));
        for (int i = 1; i <= probes; i++)
        {
            int cell = Index(from + (to - from) * ((float)i / probes));
            if (sight && cell == targetBaseCell) continue;
            if (!Walkable(cell)) return false;
        }
        return true;
    }
}
