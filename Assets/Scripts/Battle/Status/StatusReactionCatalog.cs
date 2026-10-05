using System.Collections.Generic;
using UnityEngine;

/// <summary>全状態反応の定義をResourcesから提供する共通カタログ。</summary>
[CreateAssetMenu(
    fileName = ResourceName,
    menuName = "ScriptableObjects/StatusReactionCatalog",
    order = 8)]
public sealed class StatusReactionCatalog : ScriptableObject
{
    public const string ResourceName = "StatusReactionCatalog";
    const float DefaultDamageRed = 1f;
    const float DefaultDamageGreen = 0.45f;
    const float DefaultDamageBlue = 0.12f;
    const float DefaultDamageAlpha = 1f;

    static readonly Color DefaultReactionDamageColor =
        new Color(
            DefaultDamageRed,
            DefaultDamageGreen,
            DefaultDamageBlue,
            DefaultDamageAlpha);

    [SerializeField] List<StatusReactionDefinition> definitions =
        new List<StatusReactionDefinition>();
    [SerializeField] Color reactionDamageColor = DefaultReactionDamageColor;

    static StatusReactionCatalog loaded;
    Dictionary<StatusReactionId, StatusReactionDefinition> definitionsById;

    public IReadOnlyList<StatusReactionDefinition> Definitions => definitions;
    public Color ReactionDamageColor => reactionDamageColor;

    public static StatusReactionCatalog Load()
    {
        if (loaded != null) return loaded;
        loaded = Resources.Load<StatusReactionCatalog>(ResourceName);
        return loaded;
    }

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
    static void ResetLoaded()
    {
        loaded = null;
    }

    public bool TryGet(
        StatusReactionId reactionId,
        out StatusReactionDefinition definition)
    {
        EnsureIndex();
        return definitionsById.TryGetValue(reactionId, out definition);
    }

    public void ReplaceDefinitions(
        IReadOnlyList<StatusReactionDefinition> source)
    {
        definitions.Clear();
        definitionsById = null;
        if (source == null) return;

        var seen = new HashSet<StatusReactionId>();
        for (int index = 0; index < source.Count; index++)
        {
            StatusReactionDefinition definition = source[index];
            if (definition == null) continue;
            if (seen.Add(definition.ReactionId)) definitions.Add(definition);
        }
    }

    void EnsureIndex()
    {
        if (definitionsById != null) return;
        definitionsById = new Dictionary<StatusReactionId, StatusReactionDefinition>();
        for (int index = 0; index < definitions.Count; index++)
        {
            StatusReactionDefinition definition = definitions[index];
            if (definition == null) continue;
            definitionsById[definition.ReactionId] = definition;
        }
    }

    void OnValidate()
    {
        definitionsById = null;
    }
}
