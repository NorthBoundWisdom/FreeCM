const fs = require("node:fs");
const path = require("node:path");

function sameFileIdentity(left, right) {
  return left.dev === right.dev && left.ino === right.ino;
}

function isSamePath(leftPath, rightPath) {
  return sameFileIdentity(
    fs.statSync(leftPath, { bigint: true }),
    fs.statSync(rightPath, { bigint: true }),
  );
}

function isPathWithin(rootPath, candidatePath) {
  const root = fs.statSync(rootPath, { bigint: true });
  let currentPath = fs.realpathSync(candidatePath);

  while (true) {
    if (sameFileIdentity(root, fs.statSync(currentPath, { bigint: true }))) {
      return true;
    }
    const parentPath = path.dirname(currentPath);
    if (parentPath === currentPath) {
      return false;
    }
    currentPath = parentPath;
  }
}

module.exports = { isPathWithin, isSamePath };
