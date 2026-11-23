#version 440
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec4 dataRange; // source min, source span, window center, window width
    vec2 imageSize;
    float inverted;
    int windowFunction; // 0: LINEAR_EXACT, 1: LINEAR, 2: SIGMOID
};
layout(binding = 1) uniform sampler2D source;
layout(binding = 2) uniform sampler2D colorLut;
vec2 sampleScalar(vec2 p) {
    vec4 texel = texture(source, (clamp(p,vec2(0.0),imageSize-1.0)+0.5)/imageSize);
    float normalized = dot(texel.rgb,vec3(65536.0,256.0,1.0))*(255.0/16777215.0);
    return vec2(dataRange.x+normalized*dataRange.y,texel.a);
}
void main() {
    vec2 pixel = clamp(qt_TexCoord0*imageSize-0.5,vec2(0.0),imageSize-1.0);
    vec2 p = floor(pixel), f = fract(pixel);
    vec2 a=sampleScalar(p), b=sampleScalar(p+vec2(1.0,0.0));
    vec2 c=sampleScalar(p+vec2(0.0,1.0)), d=sampleScalar(p+vec2(1.0,1.0));
    vec2 sampled = mix(mix(a,b,f.x),mix(c,d,f.x),f.y);
    float delta = sampled.x - dataRange.z;
    float width = dataRange.w;
    float index;
    if (windowFunction == 2) {
        float z = (inverted > 0.5 ? -4.0 : 4.0) * delta / width;
        float e = exp(-min(abs(z), 80.0));
        float tail = 255.0 * e / (1.0 + e);
        // Quantize the bright tail without subtracting it from 1 in float32.
        // The CPU denominator rounds to 1 once exp(-z) <= 2^-53.
        index = z > 0.0 ? (z >= 36.7368 ? 255.0 : 255.0 - ceil(tail)) : floor(tail);
    } else {
        if (windowFunction == 1) {
            // The discontinuous width-1 window uses source samples on the CPU.
            width = max(width - 1.0, 0.000001);
            delta += 0.5;
        }
        float distance = width / 2.0 + (inverted > 0.5 ? -delta : delta);
        index = floor(clamp(distance, 0.0, width) * 255.0 / width + 0.00001);
    }
    if (sampled.y < 0.99999) index=0.0;
    fragColor = texture(colorLut,vec2((index+0.5)/256.0,0.5))*qt_Opacity;
}
