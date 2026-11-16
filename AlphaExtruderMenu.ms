-- AlphaExtruder 顶部菜单自动注册脚本
(
    local mcrFile = (getDir #userMacros) + "\\AlphaExtruder-AlphaExtruder.mcr"
    if doesFileExist mcrFile do
    (
        try (fileIn mcrFile) catch ()
    )

    try
    (
        local mainMenu = menuMan.getMainMenuBar()
        if mainMenu != undefined do
        (
            local hasMenu = false
            for i = 1 to (mainMenu.numItems()) do
            (
                local it = mainMenu.getItem i
                if (it.getTitle()) == "抠图挤出" or (it.getTitle()) == "AlphaExtruder" do
                (
                    hasMenu = true
                )
            )
            if not hasMenu do
            (
                local subMenu = menuMan.findMenu "AlphaExtruder"
                if subMenu == undefined then
                (
                    subMenu = menuMan.createMenu "AlphaExtruder"
                )
                else
                (
                    while (subMenu.numItems()) > 0 do (subMenu.removeItemByPosition 1)
                )
                local actItem = menuMan.createActionItem "AlphaExtruder" "AlphaExtruder"
                if actItem != undefined do
                (
                    actItem.setTitle "透明图抠图挤出工具 (打开面板)"
                    actItem.setUseCustomTitle true
                    subMenu.addItem actItem -1
                    local subMenuItem = menuMan.createSubMenuItem "抠图挤出" subMenu
                    mainMenu.addItem subMenuItem -1
                    menuMan.updateMenuBar()
                )
            )
        )
    )
    catch ()
)
