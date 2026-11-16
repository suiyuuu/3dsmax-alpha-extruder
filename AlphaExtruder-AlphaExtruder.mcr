macroScript AlphaExtruder
    category:"AlphaExtruder"
    buttonText:"透明图抠图挤出"
    toolTip:"从透明底 PNG 一键抠出轮廓、挤出并严丝合缝贴图 (AlphaExtruder)"
(
    on execute do
    (
        local uiScript = (getDir #userScripts) + "\\AlphaExtruder\\AlphaExtruderUI.py"
        if doesFileExist uiScript then
        (
            python.ExecuteFile uiScript
        )
        else
        (
            messageBox ("找不到 AlphaExtruder 插件脚本:\n" + uiScript + "\n请先运行 install.ms 进行安装。") title:"AlphaExtruder"
        )
    )
)
