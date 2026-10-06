namespace BattleHost;

public sealed class BattleRoute(int slot, int origin)
{
    public int Slot { get; } = slot;
    public int Origin { get; set; } = origin;
    public int Destination { get; set; } = PartyRouteBoard.NoSelection;
    public List<int> Waypoints { get; } = [];
    public IEnumerable<int> Stops => Waypoints.Append(Destination);
}

/// <summary>Persistent invasion routes. Base changes affect future sorties; cell edits affect bound units.</summary>
public sealed class PartyRouteBoard
{
    public const int NoSelection = -1;
    const int FirstSlot = 0;
    const float RemainingPathWeight = 0.5f;
    const float ArrivalToleranceCells = 0.01f;
    readonly BattleSimulation simulation;
    readonly int maximumWaypoints;
    public BattleRoute[] Routes { get; }
    public int Selected { get; private set; } = NoSelection;
    public bool RestartPending { get; private set; }
    public BattleRoute Current => Selected >= 0 ? Routes[Selected] : null;

    public PartyRouteBoard(BattleSimulation simulation, int count, int waypoints)
    {
        this.simulation = simulation;
        maximumWaypoints = waypoints;
        Routes = new BattleRoute[count];
        var origin = DefaultBase();
        if (origin == null) return;
        var grid = simulation.Grid;
        var goal = simulation.Bases.Where(b => b.Team == BattleSimulation.Enemy)
            .SelectMany(b => b.OccupiedCells.SelectMany(c => grid.Adjacent(grid.Index([c.x, c.y]))))
            .Distinct().Where(c => grid.Walkable(c) && origin.SpawnIndices.Any(s => grid.Reachable(s, c)))
            .OrderBy(c => grid.Distances(c)[origin.SpawnIndices[0]]).ThenBy(c => c).FirstOrDefault(NoSelection);
        Routes[FirstSlot] = new(FirstSlot, origin.Id) { Destination = goal };
        Selected = FirstSlot;
    }
    BattleBase DefaultBase() => simulation.Bases.Where(b => b.Team == BattleSimulation.Ally && b.Alive)
        .OrderByDescending(b => b.Definition.Main).ThenBy(b => b.Id).FirstOrDefault();
    public BattleBase Origin(BattleRoute route)
    {
        if (route == null) return null;
        var origin = simulation.Bases.FirstOrDefault(b => b.Id == route.Origin && b.Team == BattleSimulation.Ally && b.Alive);
        return origin ?? DefaultBase();
    }
    public void Select(int slot)
    {
        var previous = Current;
        Routes[slot] ??= new(slot, Origin(Current)?.Id ?? DefaultBase()?.Id ?? NoSelection);
        Selected = slot;
        if (previous != null && previous.Slot != slot && previous.Destination < 0 &&
            !simulation.Units.Any(u => u.Route == previous.Slot)) Routes[previous.Slot] = null;
        RestartPending = true;
    }
    public void CancelRestart() => RestartPending = false;
    public void SetOrigin(int id)
    {
        if (Current == null) throw new InvalidOperationException("Select a route first");
        Current.Origin = id;
    }
    int[] BuildPath(int start, IEnumerable<int> stops)
    {
        var grid = simulation.Grid;
        var path = new List<int> { start };
        foreach (int stop in stops)
        {
            if (!grid.Walkable(stop) || !grid.Reachable(path[^1], stop)) return [];
            while (path[^1] != stop) path.Add(grid.Next(path[^1], stop));
        }
        return path.ToArray();
    }
    public int[] Path(BattleRoute route)
    {
        var origin = Origin(route);
        if (origin == null || route.Destination < 0) return [];
        foreach (int spawn in origin.SpawnIndices)
        {
            int[] path = BuildPath(spawn, route.Stops);
            if (path.Length > 0) return path;
        }
        return [];
    }
    int[] EditedPath(int cell)
    {
        var origin = Origin(Current);
        if (origin == null) return [];
        var stops = RestartPending || Current.Destination < 0 ? new List<int>() : Current.Waypoints.ToList();
        if (!RestartPending && Current.Destination >= 0 && stops.Count < maximumWaypoints)
            stops.Add(Current.Destination);
        stops.Add(cell);
        foreach (int spawn in origin.SpawnIndices)
        {
            int[] path = BuildPath(spawn, stops);
            if (path.Length > 0) return path;
        }
        return [];
    }
    public bool CanEdit(int cell)
    {
        if (Current == null) return false;
        int[] path = EditedPath(cell);
        return path.Length > 0 && simulation.Units.Where(u => u.Team == BattleSimulation.Ally && u.Route == Selected)
            .All(u => !u.Spawned || path.Any(c => simulation.Grid.Reachable(simulation.Grid.Index(u.Transform.position), c)));
    }
    public void Edit(int cell)
    {
        if (!CanEdit(cell)) throw new InvalidOperationException("Unreachable route");
        int[] path = EditedPath(cell);
        if (RestartPending || Current.Destination < 0) Current.Waypoints.Clear();
        else if (Current.Waypoints.Count < maximumWaypoints) Current.Waypoints.Add(Current.Destination);
        Current.Destination = cell;
        RestartPending = false;
        foreach (var unit in simulation.Units.Where(u => u.Team == BattleSimulation.Ally && u.Route == Selected))
            Assign(unit, Current, path, rejoin: true);
    }
    public void Remove(int slot)
    {
        Routes[slot] = null;
        if (Selected == slot)
            Selected = Array.FindIndex(Routes, r => r != null && r.Destination >= 0);
        RestartPending = false;
        foreach (var unit in simulation.Units.Where(u => u.Team == BattleSimulation.Ally && u.Route == slot))
        {
            unit.Route = NoSelection;
            if (Current != null)
            {
                int[] path = Path(Current);
                if (path.Length > 0 && (!unit.Spawned || path.Any(c => simulation.Grid.Reachable(simulation.Grid.Index(unit.Transform.position), c))))
                    Assign(unit, Current, path, rejoin: true);
            }
            // No replacement: retain the last path/destination and detach the binding.
        }
    }
    public void Assign(BattleUnit unit, BattleRoute route, int[] path, bool rejoin)
    {
        unit.Route = route.Slot;
        unit.Destination = route.Destination;
        unit.Path = (int[])path.Clone();
        int start = unit.Spawned ? simulation.Grid.Index(unit.Transform.position) :
            simulation.Bases.First(b => b.Id == unit.Home).SpawnIndices[0];
        unit.PathProgress = rejoin ? Enumerable.Range(0, path.Length)
            .Where(i => simulation.Grid.Reachable(start, path[i]))
            .OrderBy(i => simulation.Grid.Distances(path[i])[start] + RemainingPathWeight * (path.Length - i - 1))
            .ThenBy(i => i).FirstOrDefault() : FirstSlot;
        simulation.Squads.First(s => s.Id == unit.Squad).Destination = route.Destination;
    }
    public int MovementGoal(BattleUnit unit)
    {
        if (unit.Path.Length == 0) return unit.Destination;
        int progress = unit.PathProgress;
        if ((unit.Transform.position - simulation.Grid.World(unit.Path[progress])).magnitude <=
            ArrivalToleranceCells * GameConfig.cellSize && progress < unit.Path.Length - 1)
            unit.PathProgress++;
        return unit.Path[unit.PathProgress];
    }
}
