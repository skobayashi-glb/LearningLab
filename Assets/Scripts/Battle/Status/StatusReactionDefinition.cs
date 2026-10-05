using System;
using UnityEngine;

/// <summary>
/// 状態反応1種の成立条件・消費・効果・表示をまとめた共通データ。
/// 戦闘処理は反応名による分岐を持たず、この定義だけを解釈する。
/// </summary>
[Serializable]
public sealed class StatusReactionDefinition
{
    const float NoDurationSeconds = 0f;
    const int NoReactionDamage = 0;
    const float NeutralMultiplier = 1f;

    [SerializeField] StatusReactionId reactionId;
    [SerializeField] string displayName = "";
    [SerializeField] StatusEffectId prerequisiteStatus;
    [SerializeField] StatusEffectId triggerStatus;
    [SerializeField] StatusReactionTriggerRule triggerRule;
    [SerializeField] bool consumesPrerequisite;
    [SerializeField] bool consumesTrigger;
    [SerializeField] string effectName = "";

    [Header("即時効果")]
    [SerializeField, Min(NoReactionDamage)] int reactionDamage;
    [SerializeField] StatusEffectId durationExtensionStatus;
    [SerializeField, Min(NoDurationSeconds)] float durationExtensionSeconds;

    [Header("持続効果")]
    [SerializeField] StatusReactionLifetimePolicy lifetimePolicy;
    [SerializeField] StatusEffectId carrierStatus;
    [SerializeField, Min(NoDurationSeconds)] float durationSeconds;
    [SerializeField] StatusEffectId boostedDamageStatus;
    [SerializeField, Min(NeutralMultiplier)] float statusDamageMultiplier = NeutralMultiplier;
    [SerializeField, Min(NoDurationSeconds)] float actionBlockSeconds;
    [SerializeField, Range(NoDurationSeconds, NeutralMultiplier)]
    float movementSpeedMultiplier = NeutralMultiplier;
    [SerializeField, Min(NeutralMultiplier)]
    float positionOperationMultiplier = NeutralMultiplier;
    [SerializeField, Min(NeutralMultiplier)]
    float incomingDamageMultiplier = NeutralMultiplier;
    [SerializeField, Min(NoDurationSeconds)] float resistanceSeconds;

    public StatusReactionId ReactionId => reactionId;
    public string DisplayName => string.IsNullOrWhiteSpace(displayName)
        ? reactionId.ToString()
        : displayName;
    public StatusEffectId PrerequisiteStatus => prerequisiteStatus;
    public StatusEffectId TriggerStatus => triggerStatus;
    public StatusReactionTriggerRule TriggerRule => triggerRule;
    public bool ConsumesPrerequisite => consumesPrerequisite;
    public bool ConsumesTrigger => consumesTrigger;
    public string EffectName => effectName;
    public int ReactionDamage => Mathf.Max(NoReactionDamage, reactionDamage);
    public StatusEffectId DurationExtensionStatus => durationExtensionStatus;
    public float DurationExtensionSeconds =>
        Mathf.Max(NoDurationSeconds, durationExtensionSeconds);
    public StatusReactionLifetimePolicy LifetimePolicy => lifetimePolicy;
    public StatusEffectId CarrierStatus => carrierStatus;
    public float DurationSeconds => Mathf.Max(NoDurationSeconds, durationSeconds);
    public StatusEffectId BoostedDamageStatus => boostedDamageStatus;
    public float StatusDamageMultiplier =>
        Mathf.Max(NeutralMultiplier, statusDamageMultiplier);
    public bool BlocksActions => actionBlockSeconds > NoDurationSeconds;
    public float ActionBlockSeconds => Mathf.Max(NoDurationSeconds, actionBlockSeconds);
    public float MovementSpeedMultiplier =>
        Mathf.Clamp(movementSpeedMultiplier, NoDurationSeconds, NeutralMultiplier);
    public float PositionOperationMultiplier =>
        Mathf.Max(NeutralMultiplier, positionOperationMultiplier);
    public float IncomingDamageMultiplier =>
        Mathf.Max(NeutralMultiplier, incomingDamageMultiplier);
    public float ResistanceSeconds => Mathf.Max(NoDurationSeconds, resistanceSeconds);
    public bool ExtendsDuration => DurationExtensionSeconds > NoDurationSeconds;
    public bool BoostsStatusDamage => StatusDamageMultiplier > NeutralMultiplier;

    public void Configure(
        StatusReactionId configuredReactionId,
        string configuredDisplayName,
        StatusEffectId configuredPrerequisiteStatus,
        StatusEffectId configuredTriggerStatus,
        StatusReactionTriggerRule configuredTriggerRule,
        bool configuredConsumesPrerequisite,
        bool configuredConsumesTrigger,
        string configuredEffectName,
        int configuredReactionDamage,
        StatusEffectId configuredDurationExtensionStatus,
        float configuredDurationExtensionSeconds,
        StatusReactionLifetimePolicy configuredLifetimePolicy,
        StatusEffectId configuredCarrierStatus,
        float configuredDurationSeconds,
        StatusEffectId configuredBoostedDamageStatus,
        float configuredStatusDamageMultiplier,
        float configuredActionBlockSeconds,
        float configuredMovementSpeedMultiplier,
        float configuredPositionOperationMultiplier,
        float configuredIncomingDamageMultiplier,
        float configuredResistanceSeconds)
    {
        reactionId = configuredReactionId;
        displayName = configuredDisplayName;
        prerequisiteStatus = configuredPrerequisiteStatus;
        triggerStatus = configuredTriggerStatus;
        triggerRule = configuredTriggerRule;
        consumesPrerequisite = configuredConsumesPrerequisite;
        consumesTrigger = configuredConsumesTrigger;
        effectName = configuredEffectName;
        reactionDamage = Mathf.Max(NoReactionDamage, configuredReactionDamage);
        durationExtensionStatus = configuredDurationExtensionStatus;
        durationExtensionSeconds = Mathf.Max(
            NoDurationSeconds, configuredDurationExtensionSeconds);
        lifetimePolicy = configuredLifetimePolicy;
        carrierStatus = configuredCarrierStatus;
        durationSeconds = Mathf.Max(NoDurationSeconds, configuredDurationSeconds);
        boostedDamageStatus = configuredBoostedDamageStatus;
        statusDamageMultiplier = Mathf.Max(
            NeutralMultiplier, configuredStatusDamageMultiplier);
        actionBlockSeconds = Mathf.Max(NoDurationSeconds, configuredActionBlockSeconds);
        movementSpeedMultiplier = Mathf.Clamp(
            configuredMovementSpeedMultiplier, NoDurationSeconds, NeutralMultiplier);
        positionOperationMultiplier = Mathf.Max(
            NeutralMultiplier, configuredPositionOperationMultiplier);
        incomingDamageMultiplier = Mathf.Max(
            NeutralMultiplier, configuredIncomingDamageMultiplier);
        resistanceSeconds = Mathf.Max(NoDurationSeconds, configuredResistanceSeconds);
    }
}
