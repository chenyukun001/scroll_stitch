# 拼长图

- [简介](#简介)
- [特性](#特性)
- [功能概览](#功能概览)
    - [主窗口界面](#主窗口界面)
    - [误差修正](#误差修正)
    - [截图模式](#截图模式)
        - [整格模式](#整格模式)
        - [自由模式](#自由模式)
    - [核心操作](#核心操作)
    - [默认快捷键](#默认快捷键)
    - [配置介绍](#配置介绍)
- [快速开始](#快速开始)
- [限制](#限制)
- [安装](#安装)
    - [`python` 依赖](#python-依赖)
        - [`Ubuntu/Debian` `python` 依赖安装](#ubuntudebian-python-依赖安装)
        - [`Fedora` `python` 依赖安装](#fedora-python-依赖安装)
        - [`Arch Linux` `python` 依赖安装](#arch-linux-python-依赖安装)
        - [`openSUSE` `python` 依赖安装](#opensuse-python-依赖安装)
    - [外部依赖](#外部依赖)
        - [`Ubuntu/Debian` 外部依赖安装](#ubuntudebian-外部依赖安装)
        - [`Fedora` 外部依赖安装](#fedora-外部依赖安装)
        - [`Arch Linux` 外部依赖安装](#arch-linux-外部依赖安装)
        - [`openSUSE` 外部依赖安装](#opensuse-外部依赖安装)
    - [权限要求](#权限要求)
    - [运行程序](#运行程序)
    - [设置快捷键](#设置快捷键)
- [常见问题](#常见问题)
- [贡献](#贡献)
- [许可证](#许可证)

## 简介

一个运行在 `Linux/X11` 平台上的高度可配置的辅助式长截图工具。

![拼长图演示](assets/拼长图演示.gif)

## 特性

* **辅助截图与拼接**：通过窗口边框控制截图区域，使用图形按钮与快捷键方便截图操作，拼接时可选滚动修正减少手动误差
* **整格模式**：特定应用快速滚动截图，配合误差修正实现无缝拼接
* **系统集成**：支持桌面通知、操作音效、打开文件或目录以及自动复制到剪贴板
* **高度自定义化**：从热键、界面、输出，到布局和交互，大多可以通过图形化界面配置

## 功能概览

以下为拼长图界面与行为默认配置的简单介绍，如果想更加详细的了解，请看[详细介绍](docs/详细介绍.md)文档。

### 主窗口界面

![主窗口界面](assets/主窗口界面.png)

主窗口的界面布局是动态的，以确保不影响截图操作。

![动态布局](assets/动态布局.gif)

### 误差修正

![误差修正](assets/误差修正演示.gif)

启用误差修正后，左右边框上面的蓝色部分高度即为误差范围，拼接时只需将下边框附近内容移动到蓝色区域内即可（不要移过头），范围内的误差会被修正

### 截图模式

#### 整格模式

![整格模式演示](assets/整格模式演示.gif)

要对一个应用窗口启用整格模式，必须先配置它的滚动单位（在这个应用窗口中鼠标滚轮滚动一格时，屏幕滚动的距离的像素数）。

在整格模式下，主窗口的高度只能是滚动单位的整数倍，前进/后退的行为分别是前进一个截图区域的高度再截图、后退一个截图区域的高度并撤销。

整格模式下基本可以实现前后两张截图无缝拼接，但是在有些应用中即使滚动单位设置好了，上下两张图片还是不能完全重合，会有很小的一丝误差，这种情况可以在配置窗口中启用该应用的误差修正功能（如果没有滚动误差不建议启用该功能）。

![整格模式滚动误差](assets/整格模式滚动误差.png)

#### 自由模式

![自由模式演示](assets/自由模式演示.gif)

自由模式就是非整格模式，是程序启用时默认的模式。

自由模式下主窗口边框可以自由拖动，窗口的高度也可以是任意的，前进/后退的行为变成了前进/后退一个固定的距离。

### 核心操作

* **区域选择与调整**：主窗口边框在没有截图的时侯可以自由拖动

  ![区域选择与调整](assets/区域选择与调整.gif)

  在有截图的时候，窗口的宽度和左右边框的位置是锁死的
* **按钮功能**
    * **截图**：截取截图区域内容（不包括边框）
    * **撤销**：尝试删除截取的最后一张图片
    * **完成**：将截图拼接成一张图片并保存（完成后会有桌面通知）
    * **取消**：退出程序（退出前会有确认对话框，如果没有截图则直接退出）
    * **前进/后退**：自由模式下固定距离滚动窗口，整格模式下将窗口滚动一个截图区域的高度并截图/撤销
  
### 默认快捷键

拼长图支持快捷键操作，默认设置如下：

* **主要操作**  
  这些快捷键和对应的图形按钮的功能是一样的
    * 前进：`f`
    * 后退：`b`
    * 截图：`space`
    * 撤销：`backspace`
    * 完成：`enter`
    * 取消：`esc`
* **退出对话框操作**  

  ![退出对话框](assets/退出对话框.png)

	* 退出对话框确认：`space`
	* 退出对话框取消：`esc`
* **模式与工具**
	* 启用/禁用整格模式：`<shift>`
	* 配置滚动单位 （自由模式下）：`s`  
	  
	  ![配置滚动单位](assets/配置滚动单位.png)
	  
	  配置前，请确保界面内容丰富且有足够的滚动空间，配置时，程序会自动滚动界面，请不要移动鼠标
	* 打开/激活配置窗口：`g`  
	  
	  若配置窗口不存在则创建配置窗口，若已存在则将配置窗口激活
	* `f4`: 启用/禁用全局热键  
	  
	  程序开始时全局热键默认启用，禁用全局热键不包括其本身和打开/激活配置窗口（当主窗口本身拥有焦点时，所有快捷键还是会生效）。

### 配置介绍

拼长图提供了图形化配置窗口，可以通过它查看与自定义程序行为和界面。当鼠标悬浮在配置窗口的大部分配置项上面都会有提示文字，如果想进一步了解，可以看[详细介绍/配置指南](docs/详细介绍.md#配置指南)一节。

## 快速开始

1. **启动程序**：在终端运行命令或通过快捷键启动拼长图 （启动方法见[运行程序](#运行程序)这一部分）
2. **选择区域**：单击窗口或拖动鼠标选择初始截图区域
3. **调整区域**：按住并拖动边框调整截图框的初始大小和位置
4. **滚动并截图**：建议内容短的截图用自由模式，内容长的截图用整格模式（如果该应用配置了滚动单元）
    * 自由模式：需要记住上一张截图底部区域的位置
        * 启用误差修正：将下边框附近的内容滚动到上边框蓝色区域内，范围内的误差会自动被修正，下边框附近最好不要是大片空白
        * 不启用误差修正：将截图区域的底部恰好滚动到顶部，推荐通过调整上下边框的位置，让图片在空白区域拼接
    * 整格模式：通过前进/后退来滚动界面，在前进到内容接近结束（或超过部分）时就启用自由模式，下面手动滚动截图（默认的前进动作是滚动后截图，手动截图前需要先撤销最后一张图，再重新截取想要的区域）。
5. **完成**：按下回车，完成拼接后会有桌面通知，点击可以查看图片

## 限制

拼长图目前只能运行在 `Linux` 平台的 `X11` 会话中。

命令行输入

```shell
echo $XDG_SESSION_TYPE
```

如果输出是 `x11`，就是 `x11` 会话，如果输出是 `wayland` 就是 `wayland` 会话。

## 安装

### `python` 依赖

`python` 版本要求 3.7.4+

`python` 包版本要求：

```txt
Pillow>=3.0.0
python-xlib>=0.17
evdev>=0.6.0
pynput>=1.6.0
numpy>=1.14.5
opencv-python>=3.4.2.17
pycairo>=1.13.1
PyGObject>=3.31.2
# 对于某些 Linux 发行版（如 Ubuntu 22.04, openSUSE Leap 15.6 等）可能需要限制 PyGObject 最高版本：
# PyGObject>=3.31.2,<3.51
```

安装 `python` 包可以使用 Python 虚拟环境安装，也可以使用系统包管理器安装。

#### `Ubuntu/Debian` `python` 依赖安装

<details>
<summary>安装命令</summary>

可选：

```shell
sudo apt update
```

如果没有 `python` 的话，安装 `python`：

```shell
sudo apt install python3 python3-pip
```

可选：

```shell
pip install --upgrade pip
```

**方法一：使用 Python 虚拟环境**

先安装一部分 `PyGObject` 的系统依赖：

```shell
sudo apt install libgtk-3-dev gir1.2-notify-0.7
```

再安装依赖：

```shell
sudo apt install libgirepository-2.0-dev
```

如果没有 `venv` 包的话，先安装：

```shell
sudo apt install python3-venv
```

没有虚拟环境的话，需要创建一个（以 .venv 为例）：

```shell
python3 -m venv .venv
```

然后激活虚拟环境：

```shell
source .venv/bin/activate
```

最后在虚拟环境中安装 `python` 包：

```shell
pip install 'Pillow>=3.0.0' 'python-xlib>=0.17' 'evdev>=0.6.0' 'pynput>=1.6.0' 'opencv-python>=3.4.2.17' 'numpy>=1.14.5' 'pycairo>=1.13.1' 'PyGObject>=3.31.2'
```

安装完成后可以在终端输入

```shell
deactivate
```

退出虚拟环境，启动脚本会自动处理激活

如果安装 `libgirepository-2.0-dev` 时提示“无效的操作”，则改为：

```shell
sudo apt install libgirepository1.0-dev
```

并保证 `PyGObject>=3.31.2,<3.51`

```shell
pip install 'Pillow>=3.0.0' 'python-xlib>=0.17' 'evdev>=0.6.0' 'pynput>=1.6.0' 'opencv-python>=3.4.2.17' 'numpy>=1.14.5' 'pycairo>=1.13.1' 'PyGObject>=3.31.2,<3.51'
```

---

**方法二：使用系统包管理器安装**

安装命令：

```shell
sudo apt install python3-pil python3-xlib python3-evdev python3-pynput python3-numpy python3-opencv python3-gi python3-gi-cairo libgtk-3-dev gir1.2-notify-0.7
```

</details>

#### `Fedora` `python` 依赖安装

<details>
<summary>安装命令</summary>

如果没有 `python` 的话，安装 `python`：

```shell
sudo dnf install python3 python3-pip
```

可选：

```shell
pip install --upgrade pip
```

**方法一：使用 Python 虚拟环境（推荐）**

先安装系统依赖：

```shell
sudo dnf install gcc python3-devel cairo-gobject-devel gtk3
```

没有虚拟环境的话，需要创建一个（以 .venv 为例）：

```shell
python3 -m venv .venv
```

然后激活虚拟环境：

```shell
source .venv/bin/activate
```

最后在虚拟环境中安装 `python` 包：

```shell
pip install 'Pillow>=3.0.0' 'python-xlib>=0.17' 'evdev>=0.6.0' 'pynput>=1.6.0' 'opencv-python>=3.4.2.17' 'numpy>=1.14.5' 'pycairo>=1.13.1' 'PyGObject>=3.31.2'
```

安装完成后可以在终端输入

```shell
deactivate
```

退出虚拟环境，启动脚本会自动处理激活

如果需要 `PyGObject<3.51` 的话，还要安装：

```shell
sudo dnf install gobject-introspection-devel
```

然后安装 `python` 包命令改为：

```shell
pip install 'Pillow>=3.0.0' 'python-xlib>=0.17' 'evdev>=0.6.0' 'pynput>=1.6.0' 'opencv-python>=3.4.2.17' 'numpy>=1.14.5' 'pycairo>=1.13.1' 'PyGObject>=3.31.2,<3.51'
```

---

**方法二：使用系统包管理器安装**

安装命令：

```shell
sudo dnf install python3-xlib python3-pillow python3-evdev python3-numpy python3-opencv gtk3 python3-gobject
```

由于 `pynput` 无官方包，仍然需要通过 `pip` 安装（故推荐使用方法一）：

```shell
pip install 'pynput>=1.6.0'
```

</details>

#### `Arch Linux` `python` 依赖安装

<details>
<summary>安装命令</summary>

可选：

```shell
sudo pacman -Syu --needed
```

如果没有 `python` 的话，先安装 `python`：

```shell
sudo pacman -S --needed python python-pip
```

可选：

```shell
pip install --upgrade pip
```

**方法一：使用 Python 虚拟环境**

安装系统依赖：

```shell
sudo pacman -S --needed gcc pkgconf gtk3 gobject-introspection-runtime libnotify
```

没有虚拟环境的话，需要创建一个（以 .venv 为例）：

```shell
python3 -m venv .venv
```

然后激活虚拟环境：

```shell
source .venv/bin/activate
```

最后在虚拟环境中安装 `python` 包：

```shell
pip install 'Pillow>=3.0.0' 'python-xlib>=0.17' 'evdev>=0.6.0' 'pynput>=1.6.0' 'opencv-python>=3.4.2.17' 'numpy>=1.14.5' 'pycairo>=1.13.1' 'PyGObject>=3.31.2'
```

安装完成后可以在终端输入

```shell
deactivate
```

退出虚拟环境，启动脚本会自动处理激活

---

**方法二：使用系统包管理器安装**

安装命令：

```shell
sudo pacman -S --needed python-xlib python-pillow python-evdev python-numpy python-opencv python-gobject python-cairo gtk3 libnotify
```

由于 `pynput` 无官方包，需要从 AUR 中安装：

如果没有安装 AUR 助手，先安装（以 `paru` 为例，如果用 `yay`，请将下面命令中的 `paru` 改为 `yay`）：

```shell
sudo pacman -S --needed git base-devel && git clone https://aur.archlinux.org/paru.git && cd paru && makepkg -si
```

然后用 AUR 助手安装 `pynput`：

```shell
paru -S python-pynput
```

</details>

#### `openSUSE` `python` 依赖安装

<details>
<summary>Tumbleweed 安装命令</summary>

（这里的安装命令用 `python313` 举例，如果需要安装别的 `python` 版本的则更换安装命令中的 `python` 版本号）  

如果没有 `python` 的话，先安装 `python`：

```shell
sudo zypper install --no-recommends python313 python313-pip
```

可选：

```shell
python3.13 -m pip install --upgrade pip
```

**方法一：使用 Python 虚拟环境**

安装系统依赖：

```shell
sudo zypper install --no-recommends python313-devel gcc cairo-devel typelib-1_0-Gtk-3_0 libnotify-devel Mesa-libGL1
```

没有虚拟环境的话，需要创建一个（以 .venv 为例）：

```shell
python3.13 -m venv .venv
```

然后激活虚拟环境：

```shell
source .venv/bin/activate
```

最后在虚拟环境中安装 `python` 包：

```shell
python3.13 -m pip install 'Pillow>=3.0.0' 'python-xlib>=0.17' 'evdev>=0.6.0' 'pynput>=1.6.0' 'opencv-python>=3.4.2.17' 'numpy>=1.14.5' 'pycairo>=1.13.1' 'PyGObject>=3.31.2'
```

安装完成后可以在终端输入

```shell
deactivate
```

退出虚拟环境，启动脚本会自动处理激活

如果需要 `PyGObject<3.51` 的话，还要安装：

```shell
sudo zypper install --no-recommends gobject-introspection-devel
```

然后安装 `python` 包命令改为：

```shell
python3.13 -m pip install 'Pillow>=3.0.0' 'python-xlib>=0.17' 'evdev>=0.6.0' 'pynput>=1.6.0' 'opencv-python>=3.4.2.17' 'numpy>=1.14.5' 'pycairo>=1.13.1' 'PyGObject>=3.31.2,<3.51'
```

---

**方法二：使用系统包管理器安装**

`python31x-numpy1` 是指 `numpy` 版本 1.x，`python31x-numpy` 是指 `numpy` 版本 2.x（但是官方仓库里 `python313` 只有 `numpy`，没有 `numpy1`），可以根据 numpy 版本需要安装

```shell
sudo zypper install --no-recommends python313-python-xlib python313-Pillow python313-evdev python313-pynput python313-numpy python313-opencv python313-gobject python313-pycairo typelib-1_0-Gtk-3_0 libnotify-devel
```

</details>

<details>
<summary>Leap 安装命令</summary>

由于 Leap 官方仓库中没有 `pynput` 包，故通过虚拟环境安装

（这里的安装命令用 `python312` 举例，如果需要安装别的 `python` 版本的则更换安装命令中的 `python` 版本号）  

如果没有 `python` 的话，先安装 `python`：

```shell
sudo zypper install --no-recommends python312 python312-pip
```

可选：

```shell
python3.12 -m pip install --upgrade pip
```

**使用 Python 虚拟环境**

安装系统依赖：

```shell
sudo zypper install --no-recommends python312-devel gcc cairo-devel typelib-1_0-Gtk-3_0 libnotify-devel gobject-introspection-devel
```

> [!NOTE]
> 
> 在安装 `cairo-devel` 或 `libnotify-devel` 的过程中会因为依赖而安装 `python3.6`，并且在 `/usr/bin/` 下面创建 `python3` 指向 `python3.6` 的链接
> 
> 如果不想这样的话，可以删除这个链接：
> 
> ```shell
> sudo rm /usr/bin/python3
> ```
> 
> 或者将这个链接指向 `python3.12`：
> 
> ```shell
> sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 100
> ```

没有虚拟环境的话，需要创建一个（以 .venv 为例）：

```shell
python3.12 -m venv .venv
```

然后激活虚拟环境：

```shell
source .venv/bin/activate
```

最后在虚拟环境中安装 `python` 包：

```shell
python3.12 -m pip install 'Pillow>=3.0.0' 'python-xlib>=0.17' 'evdev>=0.6.0' 'pynput>=1.6.0' 'opencv-python>=3.4.2.17' 'numpy>=1.14.5' 'pycairo>=1.13.1' 'PyGObject>=3.31.2,<3.51'
```

安装完成后可以在终端输入

```shell
deactivate
```

退出虚拟环境，启动脚本会自动处理激活

</details>

### 外部依赖

程序的外部依赖有 `slop`、`xdg-open`、`paplay` 、`xinput` ，可以先检查一下是否安装了这些命令行工具（`command -v` 后面跟命令行工具名字，有输出就是存在）。  

`slop` 是核心依赖，必须安装，`xdg-open` 用来打开文件或目录，`paplay` 播放音效 ，`xinput` 启用隐形光标模式，可以根据需要安装。

#### `Ubuntu/Debian` 外部依赖安装

<details>
<summary>安装命令</summary>

```shell
sudo apt install slop xdg-utils pulseaudio-utils sound-theme-freedesktop xinput
```

</details>

#### `Fedora` 外部依赖安装

<details>
<summary>安装命令</summary>

```shell
sudo dnf install slop xdg-utils pulseaudio-utils sound-theme-freedesktop xinput
```

</details>

#### `Arch Linux` 外部依赖安装

<details>
<summary>安装命令</summary>

```shell
sudo pacman -S --needed slop xdg-utils pipewire-pulse sound-theme-freedesktop xorg-xinput
```

</details>

#### `openSUSE` 外部依赖安装

<details>
<summary>安装命令</summary>

```shell
sudo zypper install  --no-recommends slop xdg-utils pipewire-pulseaudio sound-theme-freedesktop
```

</details>

### 权限要求

由于程序的平滑滚动功能（包括滑块条和自由模式下的”前进/后退“滚动）以及使用隐形光标需要创建虚拟设备和主设备，所以脚本需要 `sudo` 权限运行或者将用户加入 `input` 组（推荐将用户加入 `input` 组），但是即使不提供 `sudo` 权限或未将用户加入 `input` 组，程序的核心功能仍然可用。

使用命令：

```shell
groups $USER
```

查看当前用户是否在 `input` 组里面  

运行命令

```shell
sudo usermod -aG input $USER
```

将当前用户加入到 `input` 组

然后在文件 `/etc/udev/rules.d/99-scroll_stitch.rules` 中写入内容：  

```shell
echo 'KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"' | sudo tee /etc/udev/rules.d/99-scroll_stitch.rules
```

最后重启生效

### 运行程序

依赖和权限都配置完成后，  
可以直接下载仓库中的 `scroll_stitch.py` 文件（如果是通过 Python 虚拟环境安装的依赖，还需要下载 `scroll_stitch.sh` 文件并保证两个文件在同一个目录下）或者

```shell
git clone git@github.com:chenyukun001/scroll_stitch.git
```

克隆项目到本地（下载 `config.ini` 文件并不是必须的，如果没有配置文件，程序会自动在 `~/.config/scroll_stitch` 目录下创建 `config.ini` 文件）  

<details>
<summary>虚拟环境运行方法</summary>

先赋予启动脚本执行权限：

```shell
chmod +x scroll_stitch.sh
```

然后在终端输入命令

```shell
./scroll_stitch.sh
```

即可运行程序

程序支持在命令行中用参数 `-c`（或 `--config`）传入配置文件路径以及 `-e`（或 `--venv`）传入虚拟环境路径（如果不传入该参数，则默认路径是启动脚本父目录下的 `.venv` 目录），支持相对路径（家目录 `~`，当前目录 `.` 等），如：

```shell
./scroll_stitch.sh -e ./.venv -c ~/.config/scroll_stitch/config.ini
```

如果没有传入配置文件目录，则默认会先检查当前目录下是否有配置文件，其次是 `~/.config/scroll_stitch` 目录。在配置窗口中修改的是传入的配置文件。

</details>

<details>
<summary>系统环境运行方法</summary>

直接在终端输入命令

```shell
python3 scroll_stitch.py
```

即可运行程序

程序支持在命令行中用参数 `-c`（或 `--config`）传入配置文件路径，支持相对路径（家目录 `~`，当前目录 `.` 等），如：

```shell
python3 scroll_stitch.py -c ~/.config/scroll_stitch/config.ini
```

如果没有传入配置文件目录，则默认会先检查当前目录下是否有配置文件，其次是 `~/.config/scroll_stitch` 目录。在配置窗口中修改的是传入的配置文件。

</details>

### 设置快捷键

图形化界面设置自定义快捷键的大体流程是：设置->键盘->快捷键->自定义快捷键->添加->键入命令->按下快捷键（如果支持的话）。   

键入的命令就是上面启动程序的命令，`scroll_stitch.sh` 或者 `scroll_stitch.py` 最好写成绝对路径的形式。

如果想用不同快捷键唤出不同配置的程序，可以在命令中传入不同配置文件的位置。

不同发行版和桌面环境之间设置自定义快捷键的方式可能有差异，可以查看下面的部分文档进行配置  
[https://wiki.debian.org/Keyboard/MultimediaKeys](https://wiki.debian.org/Keyboard/MultimediaKeys)  
[https://www.suse.com/support/kb/doc/?id=000019319](https://www.suse.com/support/kb/doc/?id=000019319)  
[https://docs.fedoraproject.org/zh_CN/quick-docs/gnome-setting-key-shortcut/](https://docs.fedoraproject.org/zh_CN/quick-docs/gnome-setting-key-shortcut/)

## 常见问题

1. 拖动主窗口左边框到屏幕左边缘后向右拖拽时左边框可能会卡住不动，继续向右拖拽可能导致窗口位置向右大幅度跳跃  
   
   向右缓慢拖动可缓解问题，或者干脆不启用滑块条
2. 连续快速切换整格模式和自由模式时，边框颜色可能没有切换
3. 如果误差范围内图片特征元素较少，可能导致部分内容（如纯色区域等）被压缩
4. 如果图片非常大的话，点击通知后因为渲染时间过长导致等待超时了，可能不会打开图片，需要自行在保存目录中查看
5. 主窗口始终都是在屏幕内的，在选择截图区域的时候若通过点击窗口来选取截图区域，窗口如果有部分在屏幕外，所以会不能截取屏外的这一部分窗口内容，同时主窗口位置也会有所偏差
6. 如果用户在自由模式下“前进/后退”滚动的过程中移动了鼠标，可能导致移动距离很小，整格模式下的“前进/后退”基本不受影响
7. 在部分应用中自由模式下“前进/后退”无效  
   
   这是因为默认自由滚动步长较小，增大到合适的步长即可
8. 隐形光标模式下程序退出时整个系统界面会卡顿较久  
   
   这是因为在程序退出时会删除另一个鼠标主设备，可以使用默认的移动用户光标
9. （以防万一）如果启用了隐形光标模式，并且程序中途崩溃退出，屏幕右下角留下一个光标  
   
   重启之后会自然消失，也可以终端输入 

   ```shell
   xinput list
   ```

   记下 `scroll-stitch-cursor-xxxx pointer` 的 `id`（行尾有 `master pointer` 字样），然后在终端输入 

   ```shell
   sudo xinput remove-master
   ```

   后面跟刚刚记下的 `id`，然后回车
10. 如果很快地按快捷键很多下，可能会引发一些 bug
11. 本项目为个人开发和维护，虽经测试，仍可能存在未发现的错误。

## 贡献

欢迎任何形式的贡献！
- 如果您发现了 Bug 或有任何功能建议，请随时提交 [Issue](https://github.com/chenyukun001/scroll_stitch/issues)
- 如果您想贡献代码，请 Fork 本仓库后提交 [Pull request](https://github.com/chenyukun001/scroll_stitch/pulls)

## 许可证

本项目基于 [MIT 许可证](LICENSE) 发布。