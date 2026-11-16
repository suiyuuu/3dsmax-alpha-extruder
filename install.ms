-- AlphaExtruder 一键安装脚本 (支持拖入 3ds Max 2024 - 2027 视口安装)
(
    local srcDir = getFilenamePath (getThisScriptFilename())
    local dstScriptDir = (getDir #userScripts) + "\\AlphaExtruder\\"
    local dstMacroDir = (getDir #userMacros) + "\\"
    local dstStartupDir = (getDir #userStartupScripts) + "\\"
    makeDir dstScriptDir all:true
    makeDir dstStartupDir all:true

    local files = #("AlphaExtruderCore.py", "AlphaExtruderUI.py")
    for f in files do
    (
        local src = srcDir + f
        local dst = dstScriptDir + f
        if doesFileExist dst do deleteFile dst
        copyFile src dst
    )

    local oldMcr = dstMacroDir + "CustomTools-AlphaExtruder.mcr"
    if doesFileExist oldMcr do deleteFile oldMcr

    local mcrSrc = srcDir + "AlphaExtruder-AlphaExtruder.mcr"
    local mcrDst = dstMacroDir + "AlphaExtruder-AlphaExtruder.mcr"
    if doesFileExist mcrDst do deleteFile mcrDst
    copyFile mcrSrc mcrDst

    local menuSrc = srcDir + "AlphaExtruderMenu.ms"
    local menuDst = dstStartupDir + "AlphaExtruderMenu.ms"
    if doesFileExist menuDst do deleteFile menuDst
    copyFile menuSrc menuDst

    fileIn mcrDst
    fileIn menuDst
    messageBox "AlphaExtruder 安装成功！\n\n1. 已在顶部菜单栏添加【抠图挤出 -> 透明图抠图挤出工具】\n2. 也可在【自定义 -> 自定义用户界面 -> 类别：AlphaExtruder】找到【透明图抠图挤出】\n\n现在将直接打开插件窗口。" title:"AlphaExtruder 安装完成"
    macros.run "AlphaExtruder" "AlphaExtruder"
)
