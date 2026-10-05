using System;
using System.Collections.Generic;

/// <summary>
/// 1体に残っている派生反応効果と反応耐性を保持する。
/// 成立判定や数値計算は持たず、寿命管理と通知だけを担当する。
/// </summary>
public sealed class UnitStatusReactionState
{
    const float NoRemainingSeconds = 0f;
    const float MinimumDeltaSeconds = 0f;

    sealed class ActiveReaction
    {
        public StatusReactionDefinition Definition;
        public float RemainingSeconds;
    }

    readonly Dictionary<StatusReactionId, ActiveReaction> activeById =
        new Dictionary<StatusReactionId, ActiveReaction>();
    readonly List<StatusReactionDefinition> active =
        new List<StatusReactionDefinition>();
    readonly Dictionary<StatusReactionId, float> resistanceById =
        new Dictionary<StatusReactionId, float>();
    readonly List<StatusReactionId> removalBuffer =
        new List<StatusReactionId>();
    readonly List<StatusReactionId> resistanceKeyBuffer =
        new List<StatusReactionId>();

    public event Action Changed;
    public event Action<StatusReactionDefinition> Triggered;

    public IReadOnlyList<StatusReactionDefinition> Active => active;
    public bool HasAny => active.Count > 0;

    public bool IsActive(StatusReactionId reactionId)
    {
        return activeById.ContainsKey(reactionId);
    }

    public bool CanTrigger(StatusReactionId reactionId)
    {
        return !resistanceById.TryGetValue(
                reactionId,
                out float remainingSeconds)
            || remainingSeconds <= NoRemainingSeconds;
    }

    public void Trigger(StatusReactionDefinition definition)
    {
        if (definition == null) return;
        Triggered?.Invoke(definition);
        if (definition.LifetimePolicy == StatusReactionLifetimePolicy.Instant)
        {
            return;
        }

        if (activeById.TryGetValue(
                definition.ReactionId,
                out ActiveReaction existing))
        {
            existing.Definition = definition;
            existing.RemainingSeconds = ResolveInitialRemainingSeconds(definition);
            Changed?.Invoke();
            return;
        }

        var added = new ActiveReaction
        {
            Definition = definition,
            RemainingSeconds = ResolveInitialRemainingSeconds(definition),
        };
        activeById.Add(definition.ReactionId, added);
        active.Add(definition);
        Changed?.Invoke();
    }

    public void Tick(float deltaSeconds, UnitStatusState status)
    {
        float step = Math.Max(MinimumDeltaSeconds, deltaSeconds);
        if (step <= MinimumDeltaSeconds) return;

        TickResistances(step);
        if (activeById.Count == 0) return;

        removalBuffer.Clear();
        foreach (KeyValuePair<StatusReactionId, ActiveReaction> entry in activeById)
        {
            ActiveReaction reaction = entry.Value;
            if (ShouldRemain(reaction, status, step)) continue;
            removalBuffer.Add(entry.Key);
        }
        RemoveBufferedReactions();
    }

    /// <summary>素材消費直後など、時間を進めず依存状態だけを再評価する。</summary>
    public void Refresh(UnitStatusState status)
    {
        if (activeById.Count == 0) return;
        removalBuffer.Clear();
        foreach (KeyValuePair<StatusReactionId, ActiveReaction> entry in activeById)
        {
            if (HasRequiredStatuses(entry.Value.Definition, status)) continue;
            removalBuffer.Add(entry.Key);
        }
        RemoveBufferedReactions();
    }

    public void Clear()
    {
        bool hadAny = active.Count > 0 || resistanceById.Count > 0;
        activeById.Clear();
        active.Clear();
        resistanceById.Clear();
        removalBuffer.Clear();
        resistanceKeyBuffer.Clear();
        if (hadAny) Changed?.Invoke();
    }

    static float ResolveInitialRemainingSeconds(
        StatusReactionDefinition definition)
    {
        return definition.LifetimePolicy == StatusReactionLifetimePolicy.FixedDuration
            ? Math.Max(definition.DurationSeconds, definition.ActionBlockSeconds)
            : NoRemainingSeconds;
    }

    bool ShouldRemain(
        ActiveReaction reaction,
        UnitStatusState status,
        float deltaSeconds)
    {
        StatusReactionDefinition definition = reaction.Definition;
        if (!HasRequiredStatuses(definition, status)) return false;
        if (definition.LifetimePolicy != StatusReactionLifetimePolicy.FixedDuration)
        {
            return true;
        }

        reaction.RemainingSeconds -= deltaSeconds;
        return reaction.RemainingSeconds > NoRemainingSeconds;
    }

    static bool HasRequiredStatuses(
        StatusReactionDefinition definition,
        UnitStatusState status)
    {
        if (definition == null || status == null) return false;
        switch (definition.LifetimePolicy)
        {
            case StatusReactionLifetimePolicy.WhileCarrierStatusActive:
                return status.IsActive(definition.CarrierStatus);
            case StatusReactionLifetimePolicy.WhileBothStatusesActive:
                return status.IsActive(definition.PrerequisiteStatus)
                    && status.IsActive(definition.TriggerStatus);
            default:
                return true;
        }
    }

    void RemoveBufferedReactions()
    {
        if (removalBuffer.Count == 0) return;
        for (int index = 0; index < removalBuffer.Count; index++)
        {
            StatusReactionId reactionId = removalBuffer[index];
            if (!activeById.TryGetValue(
                    reactionId,
                    out ActiveReaction removed))
            {
                continue;
            }
            activeById.Remove(reactionId);
            active.Remove(removed.Definition);
            float resistanceSeconds = removed.Definition.ResistanceSeconds;
            if (resistanceSeconds > NoRemainingSeconds)
            {
                resistanceById[reactionId] = resistanceSeconds;
            }
        }
        Changed?.Invoke();
    }

    void TickResistances(float deltaSeconds)
    {
        if (resistanceById.Count == 0) return;
        removalBuffer.Clear();
        resistanceKeyBuffer.Clear();
        foreach (StatusReactionId reactionId in resistanceById.Keys)
        {
            resistanceKeyBuffer.Add(reactionId);
        }
        for (int index = 0; index < resistanceKeyBuffer.Count; index++)
        {
            StatusReactionId reactionId = resistanceKeyBuffer[index];
            float remaining = resistanceById[reactionId] - deltaSeconds;
            if (remaining <= NoRemainingSeconds)
            {
                removalBuffer.Add(reactionId);
            }
            else
            {
                resistanceById[reactionId] = remaining;
            }
        }
        for (int index = 0; index < removalBuffer.Count; index++)
        {
            resistanceById.Remove(removalBuffer[index]);
        }
    }
}
