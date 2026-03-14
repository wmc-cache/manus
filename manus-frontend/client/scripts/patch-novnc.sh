#!/bin/bash
# 修补 noVNC 1.6.0 的顶层 await 问题
find node_modules -path "*/novnc/lib/util/browser.js" -exec sed -i \
  's/exports.supportsWebCodecsH264Decode = supportsWebCodecsH264Decode = await _checkWebCodecsH264DecodeSupport();/exports.supportsWebCodecsH264Decode = supportsWebCodecsH264Decode = false; _checkWebCodecsH264DecodeSupport().then(function(v) { exports.supportsWebCodecsH264Decode = supportsWebCodecsH264Decode = v; });/' \
  {} \;
echo "noVNC patched successfully"
