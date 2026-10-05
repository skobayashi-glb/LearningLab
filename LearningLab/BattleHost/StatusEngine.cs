using UnityEngine;

namespace BattleHost;

public sealed class StatusEngine
{
    const float Neutral = 1, Epsilon = 0.00001f;
    readonly Dictionary<int, StatusDefinition> definitions = [];
    readonly StatusReactionCatalog catalog = new();
    readonly StatusReactionResolver resolver = new();
    readonly Dictionary<string, float> settings = [];
    public StatusEngine(BattleData data)
    {
        foreach (var json in data.StatusDefinitions)
        {
            var definition = new StatusDefinition();
            Wire.ApplySerializedFields(definition, json);
            definitions.Add((int)definition.StatusId, definition);
        }
        var reactions = new List<StatusReactionDefinition>();
        foreach (var json in data.ReactionDefinitions)
        {
            var definition = new StatusReactionDefinition();
            Wire.ApplySerializedFields(definition, json);
            reactions.Add(definition);
        }
        catalog.ReplaceDefinitions(reactions);
        if (data.StatusSettings.ValueKind == System.Text.Json.JsonValueKind.Object)
            foreach (var property in data.StatusSettings.EnumerateObject())
                if (property.Value.ValueKind == System.Text.Json.JsonValueKind.Number)
                    settings[property.Name] = property.Value.GetSingle();
        if (Setting("baseApplicationChance", 1) != 1 || Setting("maximumApplicationChance", 1) != 1)
            throw new ArgumentException("Only deterministic status application is supported");
        foreach (var d in data.AllyDefinitions.Concat(data.EnemyBaseline))
            foreach (int id in d.Statuses)
            {
                if (id == (int)StatusEffectId.Sanded)
                    throw new ArgumentException("Sanded contact transmission is not supported yet");
                if (!definitions.ContainsKey(id)) throw new ArgumentException($"Missing status {id}");
            }
    }
    public float Setting(string name, float fallback) => settings.GetValueOrDefault(name, fallback);
    public bool CanAct(BattleUnit u) => !u.Reactions.Active.Any(r => r.BlocksActions);
    public float Speed(BattleUnit u)
    {
        float multiplier = u.Status.IsActive(StatusEffectId.Chill) ? Setting("chillMovementMultiplier", 0.9f) : Neutral;
        foreach (var r in u.Reactions.Active) multiplier = Math.Min(multiplier, r.MovementSpeedMultiplier);
        return CanAct(u) ? u.Definition.Speed * multiplier : 0;
    }
    public float Recovery(BattleUnit u) => u.Definition.RecoverySeconds *
        (u.Status.IsActive(StatusEffectId.Dazed) ? Setting("dazedRecoveryMultiplier", 1.1f) : Neutral);
    public int Scale(BattleTarget target, int damage, int status = -1,
        CombatDamageKind kind = CombatDamageKind.Direct)
    {
        float multiplier = Neutral;
        if (target is BattleUnit u)
        {
            if (u.Status.IsActive(StatusEffectId.Lumen)) multiplier *= Setting("lumenDamageMultiplier", 1.25f);
            foreach (var r in u.Reactions.Active)
            {
                multiplier *= r.IncomingDamageMultiplier;
                if ((int)r.BoostedDamageStatus == status) multiplier *= r.StatusDamageMultiplier;
            }
        }
        int modified = Math.Max(0, (int)MathF.Round(damage * multiplier));
        int defense = target is IDefenseTarget defended ? defended.Defense : CombatDamageCalculator.MinimumDefense;
        return CombatDamageCalculator.Resolve(modified, defense, kind);
    }
    public IEnumerable<DamageIntent> Tick(BattleUnit u, float dt, float time)
    {
        foreach (int expired in u.DotElapsed.Keys.Where(id => !u.Status.IsActive((StatusEffectId)id)).ToArray())
            u.DotElapsed.Remove(expired);
        float interval = Setting("damageIntervalSeconds", 1);
        if (interval <= 0) throw new ArgumentException("Invalid DoT interval");
        foreach (var id in u.Status.Active.ToArray())
        {
            if (!definitions.TryGetValue((int)id, out var d) || !d.DealsDamage) continue;
            float elapsed = u.DotElapsed.GetValueOrDefault((int)id);
            elapsed += Math.Min(dt, u.Status.RemainingSeconds(id));
            while (elapsed + Epsilon >= interval)
            {
                elapsed -= interval;
                yield return new(u.StatusSources.GetValueOrDefault((int)id, -1), u.Id,
                    Scale(u, d.DamagePerTick, (int)id, CombatDamageKind.Status), "status_damage", []);
                if (u.Status.IsActive(StatusEffectId.Curse) && time >= u.CurseReady)
                {
                    u.CurseReady = time + Setting("curseCooldownSeconds", 1);
                    yield return new(u.StatusSources.GetValueOrDefault((int)StatusEffectId.Curse, -1),
                        u.Id, Scale(u, (int)Setting("curseDamage", 5), kind: CombatDamageKind.Status), "curse_damage", []);
                }
            }
            u.DotElapsed[(int)id] = Math.Max(0, elapsed);
        }
        u.Status.Tick(dt);
        u.Reactions.Tick(dt, u.Status);
    }
    public int Apply(BattleUnit target, int source, int[] applied, Action<string> log)
    {
        var before = target.Status.CreateSnapshot();
        var ids = applied.Select(id => (StatusEffectId)id).ToHashSet();
        foreach (var id in ids)
        {
            target.Status.Apply(id, definitions[(int)id].DurationSeconds);
            target.StatusSources[(int)id] = source;
            log("status:" + id);
        }
        var resolution = resolver.Resolve(catalog, before, ids, target.Status, target.Reactions);
        foreach (var reaction in resolution.Triggered)
        {
            target.Reactions.Trigger(reaction);
            if (reaction.ExtendsDuration)
                target.Status.Extend(reaction.DurationExtensionStatus, reaction.DurationExtensionSeconds);
            log("reaction:" + reaction.ReactionId);
        }
        foreach (var id in resolution.Consumed) target.Status.Remove(id);
        target.Reactions.Refresh(target.Status);
        return Scale(target, resolution.TotalReactionDamage, kind: CombatDamageKind.Reaction);
    }
}
