using System.Collections.Generic;

/// <summary>成立した反応群と、一括適用する効果を保持する値オブジェクト。</summary>
public sealed class StatusReactionResolution
{
    readonly List<StatusReactionDefinition> triggered =
        new List<StatusReactionDefinition>();
    readonly HashSet<StatusEffectId> consumed =
        new HashSet<StatusEffectId>();

    public IReadOnlyList<StatusReactionDefinition> Triggered => triggered;
    public IReadOnlyCollection<StatusEffectId> Consumed => consumed;
    public int TotalReactionDamage { get; private set; }
    public bool HasAny => triggered.Count > 0;

    public void Add(StatusReactionDefinition definition)
    {
        if (definition == null) return;
        triggered.Add(definition);
        TotalReactionDamage += definition.ReactionDamage;
        if (definition.ConsumesPrerequisite)
        {
            consumed.Add(definition.PrerequisiteStatus);
        }
        if (definition.ConsumesTrigger)
        {
            consumed.Add(definition.TriggerStatus);
        }
    }
}

/// <summary>
/// 命中前・今回付与・付与後の3集合から、消費前に全反応を一括判定する。
/// Unityオブジェクトへ依存しないため、順序と同時成立を単体テストできる。
/// </summary>
public sealed class StatusReactionResolver
{
    public StatusReactionResolution Resolve(
        StatusReactionCatalog catalog,
        ISet<StatusEffectId> statusesBeforeHit,
        ISet<StatusEffectId> appliedStatuses,
        UnitStatusState statusAfterApplication,
        UnitStatusReactionState reactionState)
    {
        var resolution = new StatusReactionResolution();
        if (catalog == null
            || statusesBeforeHit == null
            || appliedStatuses == null
            || statusAfterApplication == null
            || reactionState == null)
        {
            return resolution;
        }

        IReadOnlyList<StatusReactionDefinition> definitions = catalog.Definitions;
        for (int index = 0; index < definitions.Count; index++)
        {
            StatusReactionDefinition definition = definitions[index];
            if (!CanTrigger(
                    definition,
                    statusesBeforeHit,
                    appliedStatuses,
                    statusAfterApplication,
                    reactionState))
            {
                continue;
            }
            resolution.Add(definition);
        }
        return resolution;
    }

    static bool CanTrigger(
        StatusReactionDefinition definition,
        ISet<StatusEffectId> statusesBeforeHit,
        ISet<StatusEffectId> appliedStatuses,
        UnitStatusState statusAfterApplication,
        UnitStatusReactionState reactionState)
    {
        if (definition == null
            || !reactionState.CanTrigger(definition.ReactionId)
            || !statusAfterApplication.IsActive(definition.PrerequisiteStatus)
            || !statusAfterApplication.IsActive(definition.TriggerStatus))
        {
            return false;
        }

        if (definition.TriggerRule
            == StatusReactionTriggerRule.PrerequisiteThenTrigger)
        {
            return statusesBeforeHit.Contains(definition.PrerequisiteStatus)
                && appliedStatuses.Contains(definition.TriggerStatus);
        }

        return appliedStatuses.Contains(definition.PrerequisiteStatus)
            || appliedStatuses.Contains(definition.TriggerStatus);
    }
}
