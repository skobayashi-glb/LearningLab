// Only the APIs needed by the linked pure game calculations. No Unity runtime,
// physics, rendering, Resources loading or object lifecycle is emulated.
namespace UnityEngine
{
    [AttributeUsage(AttributeTargets.Field)] public sealed class SerializeField : Attribute { }
    [AttributeUsage(AttributeTargets.Field)] public sealed class TooltipAttribute(string text) : Attribute { }
    [AttributeUsage(AttributeTargets.Field)] public sealed class HeaderAttribute(string text) : Attribute { }
    [AttributeUsage(AttributeTargets.Field)] public sealed class MinAttribute(float value) : Attribute { }
    [AttributeUsage(AttributeTargets.Field)] public sealed class RangeAttribute(float min, float max) : Attribute { }
    [AttributeUsage(AttributeTargets.Class)]
    public sealed class CreateAssetMenuAttribute : Attribute
    { public string fileName; public string menuName; public int order; }
    public enum RuntimeInitializeLoadType { SubsystemRegistration }
    [AttributeUsage(AttributeTargets.Method)]
    public sealed class RuntimeInitializeOnLoadMethodAttribute(RuntimeInitializeLoadType type) : Attribute { }
    public class ScriptableObject { }
    public static class Resources
    {
        public static T Load<T>(string name) =>
            throw new NotSupportedException("BattleHost requires explicit data; Resources.Load is forbidden");
    }
    public struct Color(float r, float g, float b, float a = 1)
    {
        public float r = r, g = g, b = b, a = a;
        public static Color white => new(1, 1, 1);
    }
    public struct Vector2(float x, float y)
    {
        public float x = x, y = y;
        public readonly float sqrMagnitude => x * x + y * y;
        public readonly float magnitude => MathF.Sqrt(sqrMagnitude);
        public static Vector2 operator +(Vector2 a, Vector2 b) => new(a.x + b.x, a.y + b.y);
        public static Vector2 operator -(Vector2 a, Vector2 b) => new(a.x - b.x, a.y - b.y);
        public static Vector2 operator *(Vector2 a, float b) => new(a.x * b, a.y * b);
        public static float Dot(Vector2 a, Vector2 b) => a.x * b.x + a.y * b.y;
    }
    public record struct Vector2Int(int x, int y);
    public struct Vector3(float x, float y, float z)
    {
        public float x = x, y = y, z = z;
        public readonly float sqrMagnitude => x * x + y * y + z * z;
        public readonly float magnitude => MathF.Sqrt(sqrMagnitude);
        public readonly Vector3 normalized => magnitude > 0 ? this / magnitude : zero;
        public void Normalize() => this = normalized;
        public static Vector3 zero => new(0, 0, 0);
        public static Vector3 one => new(1, 1, 1);
        public static Vector3 up => new(0, 1, 0);
        public static Vector3 forward => new(0, 0, 1);
        public static Vector3 operator +(Vector3 a, Vector3 b) => new(a.x + b.x, a.y + b.y, a.z + b.z);
        public static Vector3 operator -(Vector3 a, Vector3 b) => new(a.x - b.x, a.y - b.y, a.z - b.z);
        public static Vector3 operator -(Vector3 a) => new(-a.x, -a.y, -a.z);
        public static Vector3 operator *(Vector3 a, float b) => new(a.x * b, a.y * b, a.z * b);
        public static Vector3 operator *(float b, Vector3 a) => a * b;
        public static Vector3 operator /(Vector3 a, float b) => new(a.x / b, a.y / b, a.z / b);
        public static float Dot(Vector3 a, Vector3 b) => a.x * b.x + a.y * b.y + a.z * b.z;
        public static Vector3 Cross(Vector3 a, Vector3 b) =>
            new(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x);
        public static float Angle(Vector3 a, Vector3 b)
        {
            const float RadiansToDegrees = 180 / MathF.PI;
            float length = a.magnitude * b.magnitude;
            return length > 0 ? MathF.Acos(Math.Clamp(Dot(a, b) / length, -1, 1)) * RadiansToDegrees : 0;
        }
    }
    public static class Mathf
    {
        public static float Max(float a, float b) => MathF.Max(a, b);
        public static float Max(params float[] values) => values.Max();
        public static int Max(int a, int b) => Math.Max(a, b);
        public static float Min(float a, float b) => MathF.Min(a, b);
        public static float Clamp(float x, float a, float b) => Math.Clamp(x, a, b);
        public static float Clamp01(float x) => Math.Clamp(x, 0, 1);
        public static float Abs(float x) => MathF.Abs(x);
    }
    public sealed class Transform
    {
        public Vector3 position;
        public Vector3 forward = Vector3.forward;
    }
}

public static class GameConfig { public static float cellSize = 3.2f; }
public interface IDamageTarget
{
    UnityEngine.Transform Transform { get; }
    BodyBox BodyBox { get; }
}
public interface ICombatFootprintTarget : IDamageTarget
{ IReadOnlyList<UnityEngine.Vector2Int> OccupiedCells { get; } }
public static class CombatTargetReference
{
    public static bool TryGetTransform(IDamageTarget target, out UnityEngine.Transform transform)
    { transform = target?.Transform; return transform != null; }
}
public static class GridMath
{
    public static UnityEngine.Vector3 CellToWorld(UnityEngine.Vector2Int c) =>
        new(c.x * GameConfig.cellSize, 0, c.y * GameConfig.cellSize);
    public static UnityEngine.Vector3 BaseCenterWorld(IReadOnlyList<UnityEngine.Vector2Int> cells) =>
        cells.Select(CellToWorld).Aggregate(UnityEngine.Vector3.zero, (a, b) => a + b) / cells.Count;
}
public static class GroundHeight
{ public static UnityEngine.Vector3 CellToWorld(UnityEngine.Vector2Int c) => GridMath.CellToWorld(c); }
public static class CombatGeometry
{
    public static UnityEngine.Vector3 ClosestPoint(IDamageTarget target, UnityEngine.Vector3 origin)
    {
        if (target is ICombatFootprintTarget footprint)
        {
            const float HalfCell = 0.5f;
            float half = GameConfig.cellSize * HalfCell;
            return footprint.OccupiedCells.Select(c =>
            {
                var center = GridMath.CellToWorld(c);
                return new UnityEngine.Vector3(Math.Clamp(origin.x, center.x - half, center.x + half),
                    0, Math.Clamp(origin.z, center.z - half, center.z + half));
            }).OrderBy(p => (p - origin).sqrMagnitude).First();
        }
        return target.BodyBox.ClosestPoint(target.Transform.position, target.Transform.forward, origin);
    }
}
