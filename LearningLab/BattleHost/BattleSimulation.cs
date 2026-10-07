using UnityEngine;

namespace BattleHost;

public sealed class BattleSimulation
{
    public const int Ally = 0, Enemy = 1;
    const float Epsilon = 0.00001f, RangedStandOff = 0.92f;
    readonly Random enemyRandom;
    readonly Dictionary<string, UnitDefinition>[] definitions;
    readonly StatusEngine statuses;
    readonly List<BattleUnit> archive = [];
    int nextId = 1, nextSquad = 1;
    float enemyTimer;
    public BattleData Data { get; }
    public BattleGrid Grid { get; }
    public List<BattleBase> Bases { get; } = [];
    public List<BattleUnit> Units { get; } = [];
    public List<Squad> Squads { get; } = [];
    public List<BattleEvent> Events { get; } = [];
    public FixedPartyRoster Party { get; }
    public PartyRouteBoard Routes { get; }
    public EnergyWallet[] Energy { get; }
    public EnemyForecast Forecast { get; private set; }
    public int Tick { get; private set; }
    public float Time => Tick * Data.Rules.TickSeconds;
    public string Outcome { get; private set; } = "running";
    public int Captures { get; private set; }
    public bool Finished => Outcome != "running";
    public int MaxUnits { get; }
    public BattleSimulation(BattleData data, int seed, int maxUnits)
    {
        Data = data; MaxUnits = maxUnits;
        ValidateData();
        GameConfig.cellSize = data.CellSize;
        Grid = new(data.Stage);
        definitions = [data.AllyDefinitions.ToDictionary(d => d.Id), data.EnemyBaseline.ToDictionary(d => d.Id)];
        foreach (string id in data.Roster) if (!definitions[Ally].ContainsKey(id)) throw new ArgumentException("Unknown roster character");
        foreach (string id in data.EnemyPool) if (!definitions[Enemy].ContainsKey(id)) throw new ArgumentException("Unknown enemy character");
        statuses = new(data);
        enemyRandom = new Random(unchecked(seed ^ 0x5A17));
        var r = data.Rules;
        Party = new(data.Roster, r.PartySize);
        Energy = [new(r.InitialEnergy, r.MaxEnergy, r.EnergyRecoverySeconds),
                  new(r.EnemyInitialEnergy, r.MaxEnergy, r.EnergyRecoverySeconds)];
        foreach (var definition in data.Stage.Bases)
        {
            var b = new BattleBase { Id = nextId++, Team = definition.Team, Hp = definition.Hp,
                MaximumHp = definition.Hp, Definition = definition };
            b.Transform.position = GridMath.BaseCenterWorld(
                definition.Footprint.Select(c => new Vector2Int(c[0], c[1])).ToArray());
            b.SpawnIndices = definition.SpawnCells.Select(c => { Grid.RequireCell(c); return Grid.Index(c); }).ToArray();
            if (b.SpawnIndices.Length == 0 || b.SpawnIndices.Any(c => !Grid.Walkable(c)))
                throw new ArgumentException($"Base {definition.Id} has blocked/empty spawn cells");
            Bases.Add(b);
        }
        foreach (var b in Bases)
            foreach (string character in b.Definition.Garrison)
            {
                var perimeter = b.OccupiedCells.SelectMany(c => Grid.Adjacent(Grid.Index([c.x, c.y])))
                    .Distinct().Where(c => Grid.Walkable(c) && !b.SpawnIndices.Contains(c) &&
                        !Units.Any(u => u.Spawned && Grid.Index(u.Transform.position) == c)).ToArray();
                if (perimeter.Length == 0) throw new ArgumentException("No free garrison cell outside spawn ports");
                int cell = perimeter[0];
                var u = Create(character, b.Team, -1, b.Id, cell, 0);
                u.Spawned = true;
                u.Transform.position = Grid.World(cell);
            }
        foreach (var initial in data.InitialUnits)
        {
            Grid.RequireCell(initial.Cell); Grid.RequireCell(initial.Destination);
            int cell = Grid.Index(initial.Cell);
            if (!Grid.Walkable(cell)) throw new ArgumentException("Initial unit on blocked cell");
            if (initial.Team == Ally && !Party.Characters.Contains(initial.Character))
                throw new ArgumentException("Predeployed ally must be in party");
            var squad = new Squad { Id = nextSquad++, Team = initial.Team,
                Destination = Grid.Index(initial.Destination), Origin = -1 };
            Squads.Add(squad);
            var u = Create(initial.Character, initial.Team, squad.Id, -1, squad.Destination, 0);
            u.Transform.position = Grid.World(cell); u.Spawned = true;
        }
        Routes = new(this, r.RouteCount, r.MaximumWaypoints);
        if (data.OpeningWave != null) DeployOpeningWave(data.OpeningWave);
        if (Squads.Count(s => s.Team == Enemy) > r.EnemySquadLimit)
            throw new ArgumentException("Initial squad count exceeds limit");
    }
    void ValidateData()
    {
        var r = Data.Rules;
        if (Data.Version != BattleData.CurrentVersion || Data.CellSize <= 0 || r.TickSeconds <= 0 ||
            r.DecisionSeconds < r.TickSeconds || r.EpisodeSeconds <= 0 ||
            r.EnergyRecoverySeconds <= 0 || r.MaxEnergy <= 0 || r.InitialEnergy < 0 ||
            r.InitialEnergy > r.MaxEnergy || r.EnemyInitialEnergy < 0 || r.EnemyInitialEnergy > r.MaxEnergy ||
            r.PartySize <= 0 || r.RouteCount <= 0 || r.MaximumWaypoints < 0 || r.EnemySquadLimit <= 0 ||
            r.EnemyDecisionSeconds <= 0 || r.EnemyForecastSeconds < 0 || r.EnemyDuplicateLimit <= 0 ||
            r.EnemySpendMinimum <= 0 || r.EnemySpendMinimum > 1 ||
            r.SeparationCells <= 0 || r.ChainMaximumCells <= 0 || r.TurnDegreesPerSecond <= 0)
            throw new ArgumentException("Invalid battle rules");
        if (Data.Stage.Bases.Count(b => b.Main && b.Team == Ally) != 1 ||
            Data.Stage.Bases.Count(b => b.Boss && b.Team == Enemy) != 1)
            throw new ArgumentException("Exactly one ally main and enemy boss are required");
        if (Data.Stage.Bases.Any(b => b.Hp <= 0 || b.Footprint.Length == 0 || b.Team is < Ally or > Enemy))
            throw new ArgumentException("Invalid base definition");
        foreach (var d in Data.AllyDefinitions.Concat(Data.EnemyBaseline))
        {
            if (d.Hp <= 0 || d.Power <= 0 || d.Defense < CombatDamageCalculator.MinimumDefense || d.Cost <= 0 || d.Speed <= 0 ||
                d.ImpactSeconds <= 0 || d.RecoverySeconds <= 0 || d.Range <= 0)
                throw new ArgumentException($"Invalid character {d.Id}");
            if (d.MoveType != 0 || d.TargetSelection != 0 || d.KnockbackHpRatios.Length != 0)
                throw new ArgumentException($"Unsupported movement/targeting/knockback for {d.Id}; do not silently simplify");
        }
    }
    public UnitDefinition Definition(string id, int team) => definitions[team][id];
    public bool CanDeploy(int slot)
    {
        var route = Routes.Current;
        return Data.OpeningWave == null && !Finished && slot >= 0 && slot < Party.Characters.Count && route != null &&
            Definition(Party.Characters[slot], Ally).Cost <= Energy[Ally].Current && Routes.Path(route).Length > 0;
    }
    public void Deploy(int slot)
    {
        if (!CanDeploy(slot)) throw new InvalidOperationException("Illegal deployment");
        if (Units.Count >= MaxUnits) throw new InvalidOperationException("Unit capacity exceeded; enlarge observation envelope (not a gameplay limit)");
        var route = Routes.Current;
        var origin = Routes.Origin(route);
        string character = Party.Characters[slot];
        Energy[Ally].Spend(Definition(character, Ally).Cost);
        SpawnSquad(Ally, origin.Id, route.Destination, [character]);
        Routes.Assign(Units[^1], route, Routes.Path(route), rejoin: false);
    }
    BattleUnit[] SpawnSquad(int team, int origin, int destination, IReadOnlyList<string> characters)
    {
        var squad = new Squad { Id = nextSquad++, Team = team, Destination = destination, Origin = origin };
        Squads.Add(squad);
        var units = characters.Select((character, order) => Create(character, team, squad.Id, origin, destination, order)).ToArray();
        Log("deploy", origin, squad.Id, characters.Count, string.Join(",", characters));
        return units;
    }
    void DeployOpeningWave(OpeningWave wave)
    {
        if (Data.Rules.EnemyEnabled || Units.Count > 0 || wave.Ally.Length == 0 || wave.Enemy.Length == 0 ||
            wave.Ally.Length + wave.Enemy.Length > MaxUnits ||
            wave.Ally.Any(id => !Party.Characters.Contains(id)) ||
            wave.Enemy.Any(id => !definitions[Enemy].ContainsKey(id)))
            throw new ArgumentException("Opening wave requires known nonempty armies, no predeployed units, and disabled enemy AI");
        var route = Routes.Current;
        var origin = Routes.Origin(route);
        var path = route == null ? [] : Routes.Path(route);
        var enemy = Bases.Single(b => b.Definition.Boss && b.Team == Enemy);
        var main = Bases.Single(b => b.Definition.Main && b.Team == Ally);
        int destination = Grid.Index(main.Definition.Cell);
        if (origin == null || path.Length == 0 || !enemy.SpawnIndices.Any(c => Grid.Reachable(c, destination)))
            throw new ArgumentException("Opening wave requires connected invasion routes");
        // Register both armies at time zero. Spawn ports release them as space becomes available.
        // Energy is not a gate for a prescribed combat comparison; cost remains a scoring input.
        foreach (var unit in SpawnSquad(Ally, origin.Id, route.Destination, wave.Ally))
            Routes.Assign(unit, route, path, rejoin: false);
        SpawnSquad(Enemy, enemy.Id, destination, wave.Enemy);
    }
    BattleUnit Create(string character, int team, int squad, int origin, int destination, int order)
    {
        if (Units.Count >= MaxUnits) throw new InvalidOperationException("Unit capacity exceeded; enlarge envelope");
        var d = Definition(character, team);
        var u = new BattleUnit { Id = nextId++, Team = team, Definition = d,
            Hp = d.Hp, MaximumHp = d.Hp, Squad = squad, Home = origin, Destination = destination,
            Order = order };
        Units.Add(u); archive.Add(u);
        return u;
    }
    public void Advance()
    {
        if (Finished) throw new InvalidOperationException("Reset finished battle");
        int ticks = (int)MathF.Round(Data.Rules.DecisionSeconds / Data.Rules.TickSeconds);
        for (int i = 0; i < ticks && !Finished; i++) StepTick();
    }
    void StepTick()
    {
        Tick++;
        var r = Data.Rules;
        Energy[Ally].Tick(r.TickSeconds);
        Energy[Enemy].Tick(r.TickSeconds, 1 + Captures * r.EnemyAlertBonus);
        EnemyDecision();
        foreach (var u in Units.Where(u => !u.Spawned).ToArray())
        {
            var home = Bases.First(b => b.Id == u.Home);
            if (home.Team != u.Team) { u.Hp = 0; continue; }
            int cell = home.SpawnIndices.FirstOrDefault(c =>
                !Units.Any(other => other.Spawned && other.Alive &&
                    (other.Transform.position - Grid.World(c)).magnitude < r.SeparationCells * GameConfig.cellSize), -1);
            if (cell < 0) continue;
            u.Transform.position = Grid.World(cell); u.Spawned = true;
            Log("spawn", home.Id, u.Id, 0, u.Definition.Id);
        }
        var intents = new List<DamageIntent>();
        foreach (var u in Units.Where(u => u.Alive && u.Spawned).OrderBy(u => u.Id).ToArray())
        {
            u.AliveSeconds += r.TickSeconds;
            intents.AddRange(statuses.Tick(u, r.TickSeconds, Time));
            u.Cooldown = Math.Max(0, u.Cooldown - r.TickSeconds);
            if (!statuses.CanAct(u))
            { u.BlockedSeconds += r.TickSeconds; u.Attacking = false; continue; }
            if (u.Attacking)
            {
                u.Windup -= r.TickSeconds;
                if (u.Windup <= Epsilon)
                { u.Attacking = false; CollectHits(u, intents); }
                continue;
            }
            if (u.Cooldown > Epsilon) continue;
            BattleTarget target = SelectTarget(u);
            if (target != null && CanAttack(u, target))
            {
                Vector3 direction = TargetPoint(u, target) - u.Transform.position;
                Face(u, direction);
                if (Vector3.Angle(u.Transform.forward, direction) > r.AttackFacingDegrees) continue;
                u.TargetId = target.Id;
                u.Aim = u.Definition.Shape.CreateAim(u.Transform, u.BodyBox, target, AttackReach(u));
                u.Windup = u.Definition.ImpactSeconds;
                u.Cooldown = u.Windup + statuses.Recovery(u);
                u.Attacking = true;
                Log("attack_start", u.Id, target.Id);
            }
            else Move(u, target);
        }
        foreach (var hit in intents) ResolveHit(hit);
        ResolveDeathsAndCaptures();
        if (Bases.Any(b => b.Definition.Boss && b.Team == Ally)) Outcome = "victory";
        else if (Bases.Any(b => b.Definition.Main && b.Hp <= 0)) Outcome = "defeat";
        else if (Time + Epsilon >= r.EpisodeSeconds) Outcome = "timeout";
    }
    void EnemyDecision()
    {
        var r = Data.Rules;
        if (!r.EnemyEnabled) return;
        if (Forecast != null)
        {
            Forecast.Remaining -= r.TickSeconds;
            if (Forecast.Remaining <= 0)
            {
                var f = Forecast; Forecast = null;
                if (Bases.Any(b => b.Id == f.Origin && b.Team == Enemy))
                {
                    if (Units.Count + f.Characters.Count > MaxUnits)
                        throw new InvalidOperationException("Unit capacity exceeded; enlarge observation envelope");
                    SpawnSquad(Enemy, f.Origin, f.Destination, f.Characters);
                }
                else Log("forecast_cancelled", f.Origin, -1);
            }
        }
        enemyTimer += r.TickSeconds;
        if (enemyTimer + Epsilon < r.EnemyDecisionSeconds) return;
        enemyTimer -= r.EnemyDecisionSeconds;
        if (Forecast != null || Squads.Count(s => s.Team == Enemy) >= r.EnemySquadLimit) return;
        var origins = Bases.Where(b => b.Team == Enemy && b.Alive).ToArray();
        var goals = Bases.Where(b => b.Team == Ally && b.Alive).ToArray();
        var pairs = (from b in origins from g in goals
                     let goal = Grid.Index(g.Definition.Cell)
                     where b.SpawnIndices.Any(c => Grid.Reachable(c, goal))
                     select (b, goal)).ToArray();
        if (pairs.Length == 0) return;
        var (origin, destination) = pairs[enemyRandom.Next(pairs.Length)];
        int budget = Math.Max(0, (int)MathF.Floor(Energy[Enemy].Current *
            (r.EnemySpendMinimum + (1 - r.EnemySpendMinimum) * (float)enemyRandom.NextDouble())));
        var selected = new List<string>();
        while (budget > 0)
        {
            var choices = Data.EnemyPool.Where(id => Definition(id, Enemy).Cost <= budget &&
                Units.Count(u => u.Team == Enemy && u.Definition.Id == id) + selected.Count(s => s == id) < r.EnemyDuplicateLimit).ToArray();
            if (choices.Length == 0) break;
            string id = choices[enemyRandom.Next(choices.Length)];
            selected.Add(id); budget -= Definition(id, Enemy).Cost;
        }
        if (selected.Count == 0) return;
        Energy[Enemy].Spend(selected.Sum(id => Definition(id, Enemy).Cost));
        Forecast = new() { Remaining = r.EnemyForecastSeconds, Origin = origin.Id,
            Destination = destination, Characters = selected };
        Log("enemy_forecast", origin.Id, destination, selected.Count, string.Join(",", selected));
    }
    IEnumerable<BattleTarget> Opponents(BattleUnit u) =>
        Units.Where(v => v.Spawned && v.Alive && v.Team != u.Team).Cast<BattleTarget>()
            .Concat(Bases.Where(b => b.Alive && b.Team != u.Team));
    BattleTarget SelectTarget(BattleUnit u)
    {
        var enemies = Units.Where(v => v.Spawned && v.Alive && v.Team != u.Team &&
            u.Definition.Engagement.Overlaps(u.Transform.position, u.Transform.forward, v))
            .OrderBy(v => Distance(u, v)).ThenBy(v => v.Id);
        var reachable = enemies.FirstOrDefault(v => Grid.Reachable(Grid.Index(u.Transform.position), Grid.Index(v.Transform.position)));
        if (reachable != null) return reachable;
        return Bases.Where(b => b.Alive && b.Team != u.Team &&
            (b.OccupiedCells.Any(c => Grid.Index([c.x, c.y]) == u.Destination) ||
                u.Definition.Engagement.ContainsBase(u.Transform.position, u.Transform.forward, b.OccupiedCells) || CanAttack(u, b)))
            .OrderBy(b => Distance(u, b)).ThenBy(b => b.Id).FirstOrDefault();
    }
    static Vector3 TargetPoint(BattleUnit u, BattleTarget target)
    {
        var point = CombatGeometry.ClosestPoint(target, u.Transform.position);
        point.y = u.Transform.position.y;
        return point;
    }
    float AttackReach(BattleUnit u) => u.Definition.Range + (u.Definition.Ranged ? 0 : u.Definition.Lunge);
    float PreferredReach(BattleUnit u, Vector3 direction) => u.Definition.Ranged
        ? AttackReach(u) * RangedStandOff
        : Math.Min(AttackReach(u), u.BodyBox.SurfaceDistanceAlong(u.Transform.position,
            u.Transform.forward, direction) + u.Definition.Lunge + Data.Rules.ContactMarginCells * GameConfig.cellSize);
    float Distance(BattleUnit u, BattleTarget target) =>
        (TargetPoint(u, target) - u.Transform.position).magnitude;
    bool Sight(BattleUnit u, BattleTarget t)
    {
        if (u.Definition.IgnoreSight) return true;
        Vector3 point = TargetPoint(u, t);
        Vector3 delta = point - u.Transform.position;
        // Stop just outside the footprint so its own surface is not an occluder.
        const float InsetCells = 0.01f;
        if (t is BattleBase) point -= delta.normalized * (GameConfig.cellSize * InsetCells);
        return Grid.ClearSegment(u.Transform.position, point, sight: true);
    }
    bool CanAttack(BattleUnit u, BattleTarget t)
    {
        float reach = PreferredReach(u, TargetPoint(u, t) - u.Transform.position);
        return Distance(u, t) <= reach + Epsilon && Sight(u, t);
    }
    void CollectHits(BattleUnit u, List<DamageIntent> intents)
    {
        int remaining = u.Definition.MaxTargets <= 0 ? int.MaxValue : u.Definition.MaxTargets;
        foreach (var target in Opponents(u).OrderBy(t => t.Id == u.TargetId ? 0 : 1).ThenBy(t => t.Id))
        {
            bool hit = u.Definition.Shape.type == AttackShapeType.Single
                ? target.Id == u.TargetId && CanAttack(u, target)
                : u.Definition.Shape.Overlaps(u.Aim, target, AttackReach(u)) && Sight(u, target);
            if (!hit || remaining <= 0) continue;
            intents.Add(new(u.Id, target.Id, statuses.Scale(target, u.Definition.Power), "direct_damage", u.Definition.Statuses));
            remaining--;
        }
    }
    void Face(BattleUnit u, Vector3 direction)
    {
        if (direction.sqrMagnitude <= Epsilon) return;
        float from = MathF.Atan2(u.Transform.forward.x, u.Transform.forward.z);
        float to = MathF.Atan2(direction.x, direction.z);
        float delta = MathF.Atan2(MathF.Sin(to - from), MathF.Cos(to - from));
        const float DegreesToRadians = MathF.PI / 180;
        float step = Data.Rules.TurnDegreesPerSecond * DegreesToRadians * Data.Rules.TickSeconds;
        float angle = from + Math.Clamp(delta, -step, step);
        u.Transform.forward = new(MathF.Sin(angle), 0, MathF.Cos(angle));
    }
    void Move(BattleUnit u, BattleTarget target)
    {
        int current = Grid.Index(u.Transform.position);
        int goal = target is BattleUnit other ? Grid.Index(other.Transform.position) :
            u.Team == Ally ? Routes.MovementGoal(u) : u.Destination;
        if (target is BattleBase b) goal = Grid.Index(b.Definition.Cell);
        Vector3 desired;
        if (target != null && Sight(u, target))
        {
            var point = TargetPoint(u, target);
            var toward = point - u.Transform.position;
            float stand = PreferredReach(u, toward);
            // Do not back away when already inside the preferred attack distance.
            desired = u.Transform.position + toward.normalized * Math.Max(0, toward.magnitude - stand);
        }
        else
        {
            int next = Grid.Next(current, goal);
            desired = Grid.World(next);
            if (next == current && current != goal) return;
        }
        var leader = Units.Where(v => v.Squad == u.Squad && u.Squad >= 0 && v.Spawned && v.Order < u.Order)
            .OrderByDescending(v => v.Order).FirstOrDefault();
        if (target == null && leader != null &&
            (leader.Transform.position - u.Transform.position).magnitude > Data.Rules.ChainMaximumCells * GameConfig.cellSize)
            desired = leader.Transform.position;
        Vector3 delta = desired - u.Transform.position;
        Face(u, delta);
        float amount = Math.Min(delta.magnitude, statuses.Speed(u) * GameConfig.cellSize * Data.Rules.TickSeconds);
        Vector3 nextPosition = u.Transform.position + delta.normalized * amount;
        float separation = Data.Rules.SeparationCells * GameConfig.cellSize;
        foreach (var v in Units.Where(v => v != u && v.Spawned && v.Alive))
        {
            float oldDistance = (u.Transform.position - v.Transform.position).magnitude;
            float newDistance = (nextPosition - v.Transform.position).magnitude;
            if (newDistance < separation && newDistance < oldDistance - Epsilon) return;
        }
        if (Grid.ClearSegment(u.Transform.position, nextPosition)) u.Transform.position = nextPosition;
    }
    void ResolveHit(DamageIntent intent)
    {
        BattleTarget target = Units.Cast<BattleTarget>().Concat(Bases).FirstOrDefault(t => t.Id == intent.Target);
        if (target == null) return;
        int effective = Math.Min(Math.Max(0, target.Hp), intent.Damage);
        target.Hp -= intent.Damage;
        var source = archive.FirstOrDefault(u => u.Id == intent.Source);
        if (source != null) source.DamageDone += effective;
        Log(intent.Kind, intent.Source, target.Id, effective);
        if (target is BattleUnit unit && target.Alive && intent.Statuses.Length > 0)
        {
            int reaction = statuses.Apply(unit, intent.Source, intent.Statuses,
                text => Log("status_event", intent.Source, unit.Id, 0, text));
            if (reaction > 0) ResolveHit(new(intent.Source, target.Id, reaction, "reaction_damage", []));
        }
    }
    void ResolveDeathsAndCaptures()
    {
        foreach (var u in Units.Where(u => !u.Alive).ToArray())
        {
            Units.Remove(u);
            Log("death", -1, u.Id, 0, u.Definition.Id);
        }
        Squads.RemoveAll(s => !Units.Any(u => u.Squad == s.Id));
        foreach (var b in Bases.Where(b => b.Hp <= 0 && !b.Definition.Main))
        {
            b.Team = 1 - b.Team; b.Hp = b.MaximumHp;
            if (b.Team == Ally) Captures++;
            Log("capture", -1, b.Id, b.Team);
        }
    }
    void Log(string kind, int source, int target, float amount = 0, string detail = "") =>
        Events.Add(new(Tick, kind, source, target, amount, detail));
    public object Metrics() => archive.GroupBy(u => (u.Team, u.Definition.Id))
        .Select(g => new { team = g.Key.Team, character = g.Key.Id,
            deployed = g.Count(), deaths = g.Count(u => !u.Alive),
            damage = g.Sum(u => u.DamageDone), alive_seconds = g.Sum(u => u.AliveSeconds),
            blocked_seconds = g.Sum(u => u.BlockedSeconds) }).ToArray();
}
