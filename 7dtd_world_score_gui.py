#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import dataclasses
import math
import os
import pathlib
import re
import sys
import tkinter
import tkinter.filedialog
import tkinter.messagebox
import tkinter.ttk
import xml.etree.ElementTree

try:
	import numpy
except ImportError:
	print("Missing dependency: numpy. Install with: python3 -m pip install numpy", file=sys.stderr)
	raise

try:
	from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk
except ImportError:
	print("Missing dependency: pillow. Install with: python3 -m pip install pillow", file=sys.stderr)
	raise


@dataclasses.dataclass
class PrefabPlacement:
	Name: str
	WorldX: float
	WorldY: float
	WorldZ: float
	PixelX: int = -1
	PixelY: int = -1
	Tier: int | None = None
	TierSource: str = "unknown"
	IsTrader: bool = False
	InMap: bool = False


@dataclasses.dataclass
class CoordinateTransform:
	Name: str
	ToPixel: object
	ToWorld: object
	PixelsPerWorldUnit: float = 1.0


KNOWN_BIOME_COLORS = [
	("Forest", (0, 64, 0)),
	("Burnt forest", (186, 0, 255)),
	("Desert", (255, 228, 0)),
	("Desert", (255, 255, 0)),
	("Snow", (255, 255, 255)),
	("Wasteland", (0, 255, 0)),
	("Radiation", (255, 0, 0)),
]


EXCLUDED_PREFAB_NAME_PARTS = [
	"/parts/",
	"\\parts\\",
	"/tiles/",
	"\\tiles\\",
	"/decorations/",
	"\\decorations\\",
]

EXCLUDED_PREFAB_PREFIXES = [
	"part_",
	"parts_",
	"rwg_tile_",
	"tile_",
	"street_",
	"road_",
	"deco_",
	"decoration_",
]


class WorldScoreApp:
	def __init__(self, Root: tkinter.Tk):
		self.Root = Root
		self.Root.title("7DTD world POI score map")

		self.WorldFolder = tkinter.StringVar()
		self.PrefabsFolder = tkinter.StringVar()
		self.TraderDistanceCoefficient = tkinter.DoubleVar(value=3.0)
		self.MinTier = tkinter.IntVar(value=5)
		self.MaxDistCoeff = tkinter.DoubleVar(value=1500.0)
		self.MaxTraderDist = tkinter.DoubleVar(value=500.0)
		self.StrictTierComparison = tkinter.BooleanVar(value=False)
		self.BiomeBoundaryWidth = tkinter.IntVar(value=7)
		self.PreviewMaxSize = tkinter.IntVar(value=1100)

		self.ImageWidth = 0
		self.ImageHeight = 0
		self.Transform: CoordinateTransform | None = None
		self.Placements: list[PrefabPlacement] = []
		self.Traders: list[PrefabPlacement] = []
		self.ScorePrefabs: list[PrefabPlacement] = []
		self.Score: numpy.ndarray | None = None
		self.NormalizedScore: numpy.ndarray | None = None
		self.RenderImage: Image.Image | None = None
		self.PreviewImage: Image.Image | None = None
		self.PreviewPhoto: ImageTk.PhotoImage | None = None
		self.PreviewScale = 1.0
		self.ViewScale = 1.0
		self.ViewOffsetX = 0.0
		self.ViewOffsetY = 0.0
		self.CanvasImageItem = None
		self.DragStartX = 0
		self.DragStartY = 0
		self.DragLastX = 0
		self.DragLastY = 0
		self.DragMoved = False

		self.ScriptPath = pathlib.Path(__file__).resolve()
		self.ScriptDir = self.ScriptPath.parent
		self.OutputDir = self.ScriptDir / "outputs"
		self.SettingsPath = self.ScriptDir / "settings.xml"
		self.FilteredLogPath = self.OutputDir / "poi_filtered.log"

		self._BuildGui()
		self._LoadSettings()
		if not self.PrefabsFolder.get().strip():
			self._GuessPrefabsFolder()

	def _BuildGui(self):
		Main = tkinter.ttk.Frame(self.Root, padding=8)
		Main.grid(row=0, column=0, sticky="nsew")
		self.Root.rowconfigure(0, weight=1)
		self.Root.columnconfigure(0, weight=1)

		Controls = tkinter.ttk.Frame(Main)
		Controls.grid(row=0, column=0, sticky="ew")
		Controls.columnconfigure(1, weight=1)
		Controls.columnconfigure(4, weight=1)

		tkinter.ttk.Button(Controls, text="World folder...", command=self._ChooseWorldFolder).grid(row=0, column=0, sticky="w")
		tkinter.ttk.Entry(Controls, textvariable=self.WorldFolder).grid(row=0, column=1, columnspan=3, sticky="ew", padx=4)
		tkinter.ttk.Button(Controls, text="Game Data/Prefabs...", command=self._ChoosePrefabsFolder).grid(row=0, column=4, sticky="w", padx=(10, 0))
		tkinter.ttk.Entry(Controls, textvariable=self.PrefabsFolder).grid(row=0, column=5, columnspan=3, sticky="ew", padx=4)

		tkinter.ttk.Label(Controls, text="Trader coeff").grid(row=1, column=0, sticky="e", pady=(6, 0))
		tkinter.ttk.Spinbox(Controls, from_=0.0, to=100.0, increment=0.25, textvariable=self.TraderDistanceCoefficient, width=8).grid(row=1, column=1, sticky="w", pady=(6, 0))

		tkinter.ttk.Label(Controls, text="Min tier").grid(row=1, column=2, sticky="e", pady=(6, 0))
		tkinter.ttk.Spinbox(Controls, from_=0, to=10, increment=1, textvariable=self.MinTier, width=8).grid(row=1, column=3, sticky="w", pady=(6, 0))

		tkinter.ttk.Label(Controls, text="POI radius / MaxDistCoeff").grid(row=1, column=4, sticky="e", pady=(6, 0))
		tkinter.ttk.Spinbox(Controls, from_=0.0, to=10000.0, increment=100.0, textvariable=self.MaxDistCoeff, width=10).grid(row=1, column=5, sticky="w", pady=(6, 0))

		tkinter.ttk.Label(Controls, text="Trader radius / MaxTraderDist").grid(row=1, column=6, sticky="e", pady=(6, 0))
		tkinter.ttk.Spinbox(Controls, from_=0.0, to=10000.0, increment=100.0, textvariable=self.MaxTraderDist, width=10).grid(row=1, column=7, sticky="w", pady=(6, 0))

		tkinter.ttk.Checkbutton(Controls, text="Strict tier > MinTier", variable=self.StrictTierComparison).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
		tkinter.ttk.Label(Controls, text="Biome line width").grid(row=2, column=2, sticky="e", pady=(6, 0))
		tkinter.ttk.Spinbox(Controls, from_=1, to=31, increment=2, textvariable=self.BiomeBoundaryWidth, width=8).grid(row=2, column=3, sticky="w", pady=(6, 0))
		tkinter.ttk.Label(Controls, text="Preview max px").grid(row=2, column=4, sticky="e", pady=(6, 0))
		tkinter.ttk.Spinbox(Controls, from_=300, to=3000, increment=100, textvariable=self.PreviewMaxSize, width=10).grid(row=2, column=5, sticky="w", pady=(6, 0))
		tkinter.ttk.Button(Controls, text="Analyze / redraw", command=self._Analyze).grid(row=2, column=6, columnspan=2, sticky="ew", padx=(10, 0), pady=(6, 0))

		self.Status = tkinter.StringVar(value="Choose a generated world folder.")
		tkinter.ttk.Label(Main, textvariable=self.Status).grid(row=1, column=0, sticky="ew", pady=(6, 0))

		Paned = tkinter.ttk.Panedwindow(Main, orient="horizontal")
		Paned.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
		Main.rowconfigure(2, weight=1)
		Main.columnconfigure(0, weight=1)

		CanvasFrame = tkinter.ttk.Frame(Paned)
		CanvasFrame.rowconfigure(0, weight=1)
		CanvasFrame.columnconfigure(0, weight=1)
		self.Canvas = tkinter.Canvas(CanvasFrame, background="#202020", width=900, height=900, highlightthickness=0)
		self.Canvas.grid(row=0, column=0, sticky="nsew")
		self.Canvas.bind("<ButtonPress-1>", self._StartMapDrag)
		self.Canvas.bind("<B1-Motion>", self._DragMap)
		self.Canvas.bind("<ButtonRelease-1>", self._EndMapDrag)
		self.Canvas.bind("<MouseWheel>", self._ZoomMap)
		self.Canvas.bind("<Button-4>", self._ZoomMap)
		self.Canvas.bind("<Button-5>", self._ZoomMap)
		self.Canvas.bind("<Configure>", self._ResizeMapView)
		Paned.add(CanvasFrame, weight=4)

		InfoFrame = tkinter.ttk.Frame(Paned)
		InfoFrame.rowconfigure(0, weight=1)
		InfoFrame.columnconfigure(0, weight=1)
		self.InfoText = tkinter.Text(InfoFrame, width=58, wrap="word")
		self.InfoText.grid(row=0, column=0, sticky="nsew")
		InfoScroll = tkinter.ttk.Scrollbar(InfoFrame, orient="vertical", command=self.InfoText.yview)
		InfoScroll.grid(row=0, column=1, sticky="ns")
		self.InfoText.configure(yscrollcommand=InfoScroll.set)
		Paned.add(InfoFrame, weight=2)

	def _ChooseWorldFolder(self):
		Folder = tkinter.filedialog.askdirectory(
			title="Select 7 Days to Die GeneratedWorlds/<World> folder",
			initialdir=self.WorldFolder.get().strip() or None,
		)
		if Folder:
			self.WorldFolder.set(Folder)
			self._SaveSettings()

	def _ChoosePrefabsFolder(self):
		Folder = tkinter.filedialog.askdirectory(
			title="Select 7 Days to Die/Data/Prefabs folder",
			initialdir=self.PrefabsFolder.get().strip() or None,
		)
		if Folder:
			self.PrefabsFolder.set(Folder)
			self._SaveSettings()

	def _LoadSettings(self):
		try:
			Root = xml.etree.ElementTree.parse(self.SettingsPath).getroot()
		except (FileNotFoundError, xml.etree.ElementTree.ParseError, OSError):
			return
		WorldFolder = Root.findtext("WorldFolder", default="").strip()
		PrefabsFolder = Root.findtext("PrefabsFolder", default="").strip()
		if WorldFolder:
			self.WorldFolder.set(WorldFolder)
		if PrefabsFolder:
			self.PrefabsFolder.set(PrefabsFolder)

	def _SaveSettings(self):
		Root = xml.etree.ElementTree.Element("Settings")
		xml.etree.ElementTree.SubElement(Root, "WorldFolder").text = self.WorldFolder.get().strip()
		xml.etree.ElementTree.SubElement(Root, "PrefabsFolder").text = self.PrefabsFolder.get().strip()
		Tree = xml.etree.ElementTree.ElementTree(Root)
		try:
			xml.etree.ElementTree.indent(Tree, space="	")
		except AttributeError:
			pass
		Tree.write(self.SettingsPath, encoding="utf-8", xml_declaration=True)

	def _GuessPrefabsFolder(self):
		Candidates = [
			pathlib.Path.home() / ".local/share/Steam/steamapps/common/7 Days To Die/Data/Prefabs",
			pathlib.Path.home() / ".steam/steam/steamapps/common/7 Days To Die/Data/Prefabs",
			pathlib.Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common/7 Days To Die/Data/Prefabs",
			pathlib.Path("C:/Program Files (x86)/Steam/steamapps/common/7 Days To Die/Data/Prefabs"),
			pathlib.Path("C:/Program Files/Steam/steamapps/common/7 Days To Die/Data/Prefabs"),
		]
		for Candidate in Candidates:
			if Candidate.exists() and Candidate.is_dir():
				self.PrefabsFolder.set(str(Candidate))
				return

	def _SetStatus(self, Text: str):
		self.Status.set(Text)
		self.Root.update_idletasks()

	def _Analyze(self):
		try:
			WorldPath = pathlib.Path(self.WorldFolder.get()).expanduser()
			if not WorldPath.exists() or not WorldPath.is_dir():
				raise ValueError("Choose a valid generated world folder.")

			BiomesPath = WorldPath / "biomes.png"
			PrefabsXmlPath = WorldPath / "prefabs.xml"
			if not BiomesPath.exists():
				raise ValueError(f"Missing file: {BiomesPath}")
			if not PrefabsXmlPath.exists():
				raise ValueError(f"Missing file: {PrefabsXmlPath}")

			self._SetStatus("Loading biomes.png...")
			BiomesImage = Image.open(BiomesPath).convert("RGB")
			Biomes = numpy.asarray(BiomesImage)
			self.ImageWidth, self.ImageHeight = BiomesImage.size

			self._SetStatus("Parsing prefabs.xml...")
			Placements = ParseWorldPrefabs(PrefabsXmlPath)

			PrefabTierIndex = {}
			PrefabsFolderText = self.PrefabsFolder.get().strip()
			if PrefabsFolderText:
				PrefabsFolder = pathlib.Path(PrefabsFolderText).expanduser()
				if PrefabsFolder.exists() and PrefabsFolder.is_dir():
					self._SetStatus("Indexing prefab DifficultyTier from Data/Prefabs XML files...")
					PrefabTierIndex = BuildPrefabTierIndex(PrefabsFolder)

			ApplyPrefabTiers(Placements, PrefabTierIndex)

			self._SetStatus("Choosing coordinate transform...")
			# Determine the generated world dimensions separately from the biome image size.
			# Some 7DTD maps ship a scaled biomes.png (for example 1280 px for an
			# 8192 m world), while prefabs.xml always stores world-meter coordinates.
			WorldWidth, WorldHeight = GetGeneratedWorldSize(WorldPath, Placements, self.ImageWidth, self.ImageHeight)
			self.Transform = ChooseBestCoordinateTransform(Placements, self.ImageWidth, self.ImageHeight, WorldWidth, WorldHeight)
			for Placement in Placements:
				PixelX, PixelY = self.Transform.ToPixel(Placement.WorldX, Placement.WorldZ, self.ImageWidth, self.ImageHeight)
				Placement.PixelX = int(round(PixelX))
				Placement.PixelY = int(round(PixelY))
				Placement.InMap = 0 <= Placement.PixelX < self.ImageWidth and 0 <= Placement.PixelY < self.ImageHeight

			self.Placements = [P for P in Placements if P.InMap]
			self.Traders = [P for P in self.Placements if P.IsTrader]
			self.ScorePrefabs, FilteredPrefabs = self._FilterScorePrefabs(Placements)
			self.OutputDir.mkdir(parents=True, exist_ok=True)
			WriteFilteredPoiLog(self.FilteredLogPath, FilteredPrefabs)
			self._SaveSettings()

			self._SetStatus("Computing score heatmap...")
			self.Score = ComputeScoreMap(
				self.ImageWidth,
				self.ImageHeight,
				self.Traders,
				self.ScorePrefabs,
				float(self.TraderDistanceCoefficient.get()),
				float(self.MaxTraderDist.get()),
				float(self.MaxDistCoeff.get()),
				self.Transform.PixelsPerWorldUnit if self.Transform else 1.0,
				self._SetStatus,
			)
			self.NormalizedScore = NormalizeScore(self.Score)

			self._SetStatus("Rendering map overlays...")
			self.RenderImage = RenderMap(
				self.NormalizedScore,
				Biomes,
				self.Traders,
				self.ScorePrefabs,
				int(self.BiomeBoundaryWidth.get()),
			)

			OutputPath = self._BuildOutputImagePath(WorldPath)
			OutputPath.parent.mkdir(parents=True, exist_ok=True)
			self.RenderImage.save(OutputPath)
			self._ShowPreview()
			self._WriteSummary(OutputPath)
			self._SetStatus(f"Done. Saved: {OutputPath}")
		except Exception as Error:
			tkinter.messagebox.showerror("7DTD world POI score map", str(Error), parent=self.Root)
			self._SetStatus(f"Error: {Error}")


	def _BuildOutputImagePath(self, WorldPath: pathlib.Path) -> pathlib.Path:
		WorldName = SanitizePathPart(WorldPath.name.strip() or "world")
		return self.OutputDir / "poi_score_maps" / f"{WorldName}.png"

	def _FilterScorePrefabs(self, Placements: list[PrefabPlacement]) -> tuple[list[PrefabPlacement], list[tuple[PrefabPlacement, str]]]:
		MinTier = int(self.MinTier.get())
		Strict = bool(self.StrictTierComparison.get())
		Result = []
		Filtered = []
		for Placement in Placements:
			Reason = GetScorePrefabExclusionReason(Placement, MinTier, Strict)
			if Reason is None:
				Result.append(Placement)
			else:
				Filtered.append((Placement, Reason))
		return Result, Filtered

	def _ShowPreview(self):
		if self.RenderImage is None:
			return
		MaxSize = max(100, int(self.PreviewMaxSize.get()))
		Scale = min(MaxSize / self.RenderImage.width, MaxSize / self.RenderImage.height, 1.0)
		self.PreviewScale = Scale
		self.ViewScale = Scale
		self.ViewOffsetX = 0.0
		self.ViewOffsetY = 0.0
		self.Canvas.delete("all")
		self.CanvasImageItem = None
		self._RenderCanvasImage()

	def _RenderCanvasImage(self):
		if self.RenderImage is None:
			return
		Scale = max(0.01, min(1.0, float(self.ViewScale)))
		PreviewSize = (max(1, int(self.RenderImage.width * Scale)), max(1, int(self.RenderImage.height * Scale)))
		self.ViewScale = Scale
		self.PreviewScale = Scale
		Resample = Image.Resampling.LANCZOS if Scale < 1.0 else Image.Resampling.NEAREST
		self.PreviewImage = self.RenderImage.resize(PreviewSize, Resample)
		self.PreviewPhoto = ImageTk.PhotoImage(self.PreviewImage)
		self._ClampViewOffset(PreviewSize)
		if self.CanvasImageItem is None:
			self.CanvasImageItem = self.Canvas.create_image(self.ViewOffsetX, self.ViewOffsetY, anchor="nw", image=self.PreviewPhoto)
		else:
			self.Canvas.itemconfigure(self.CanvasImageItem, image=self.PreviewPhoto)
			self.Canvas.coords(self.CanvasImageItem, self.ViewOffsetX, self.ViewOffsetY)
		self.Canvas.configure(scrollregion=(self.ViewOffsetX, self.ViewOffsetY, self.ViewOffsetX + PreviewSize[0], self.ViewOffsetY + PreviewSize[1]))

	def _ClampViewOffset(self, PreviewSize: tuple[int, int] | None = None):
		if self.RenderImage is None:
			return
		if PreviewSize is None:
			PreviewSize = (max(1, int(self.RenderImage.width * self.ViewScale)), max(1, int(self.RenderImage.height * self.ViewScale)))
		CanvasWidth = max(1, self.Canvas.winfo_width())
		CanvasHeight = max(1, self.Canvas.winfo_height())
		ImageWidth, ImageHeight = PreviewSize
		if ImageWidth <= CanvasWidth:
			self.ViewOffsetX = (CanvasWidth - ImageWidth) / 2
		else:
			self.ViewOffsetX = min(0.0, max(CanvasWidth - ImageWidth, self.ViewOffsetX))
		if ImageHeight <= CanvasHeight:
			self.ViewOffsetY = (CanvasHeight - ImageHeight) / 2
		else:
			self.ViewOffsetY = min(0.0, max(CanvasHeight - ImageHeight, self.ViewOffsetY))

	def _ResizeMapView(self, Event):
		if self.RenderImage is None or self.CanvasImageItem is None:
			return
		self._ClampViewOffset()
		self.Canvas.coords(self.CanvasImageItem, self.ViewOffsetX, self.ViewOffsetY)

	def _ZoomMap(self, Event):
		if self.RenderImage is None:
			return "break"
		if getattr(Event, "num", None) == 4 or getattr(Event, "delta", 0) > 0:
			ZoomFactor = 1.25
		else:
			ZoomFactor = 0.8
		OldScale = self.ViewScale
		NewScale = max(0.02, min(1.0, OldScale * ZoomFactor))
		if abs(NewScale - OldScale) < 0.000001:
			return "break"
		ImageX = (Event.x - self.ViewOffsetX) / OldScale
		ImageY = (Event.y - self.ViewOffsetY) / OldScale
		self.ViewScale = NewScale
		self.ViewOffsetX = Event.x - ImageX * NewScale
		self.ViewOffsetY = Event.y - ImageY * NewScale
		self._RenderCanvasImage()
		return "break"

	def _StartMapDrag(self, Event):
		self.DragStartX = self.DragLastX = Event.x
		self.DragStartY = self.DragLastY = Event.y
		self.DragMoved = False
		self.Canvas.configure(cursor="fleur")

	def _DragMap(self, Event):
		if self.RenderImage is None:
			return
		DeltaX = Event.x - self.DragLastX
		DeltaY = Event.y - self.DragLastY
		if abs(Event.x - self.DragStartX) > 3 or abs(Event.y - self.DragStartY) > 3:
			self.DragMoved = True
		self.DragLastX = Event.x
		self.DragLastY = Event.y
		self.ViewOffsetX += DeltaX
		self.ViewOffsetY += DeltaY
		self._ClampViewOffset()
		if self.CanvasImageItem is not None:
			self.Canvas.coords(self.CanvasImageItem, self.ViewOffsetX, self.ViewOffsetY)

	def _EndMapDrag(self, Event):
		self.Canvas.configure(cursor="")
		if not self.DragMoved:
			self._InspectCanvasPoint(Event)


	def _WriteSummary(self, OutputPath: pathlib.Path):
		UnknownTierCount = sum(1 for P in self.Placements if P.Tier is None and not P.IsTrader and not IsExcludedPrefabName(P.Name))
		TierCounts = {}
		for Placement in self.Placements:
			if Placement.Tier is not None:
				TierCounts[Placement.Tier] = TierCounts.get(Placement.Tier, 0) + 1

		Text = []
		Text.append(f"World: {self.WorldFolder.get()}\n")
		Text.append(f"Output: {OutputPath}\n")
		Text.append(f"Map size: {self.ImageWidth} x {self.ImageHeight} px\n")
		Text.append(f"Coordinate transform: {self.Transform.Name if self.Transform else '-'}\n")
		Text.append(f"Placements in map: {len(self.Placements)}\n")
		Text.append(f"Traders: {len(self.Traders)}\n")
		Text.append(f"Score POIs: {len(self.ScorePrefabs)}\n")
		Text.append(f"Unknown-tier non-trader prefabs skipped: {UnknownTierCount}\n")
		Text.append("Tier counts in map: " + ", ".join(f"T{K}: {TierCounts[K]}" for K in sorted(TierCounts)) + "\n")
		if self.Score is not None:
			Text.append(f"Raw score min/max: {float(numpy.min(self.Score)):.3f} / {float(numpy.max(self.Score)):.3f}\n")
		Text.append("\nClick on the map preview to inspect a point.\n")
		Text.append("\nTop score prefabs used:\n")
		for Placement in sorted(self.ScorePrefabs, key=lambda P: (-P.Tier if P.Tier is not None else 0, P.Name))[:80]:
			Text.append(f"  T{Placement.Tier} {Placement.Name} @ E/W {Placement.WorldX:.0f}, N/S {Placement.WorldZ:.0f}\n")
		self._SetInfoText("".join(Text))

	def _SetInfoText(self, Text: str):
		self.InfoText.delete("1.0", "end")
		self.InfoText.insert("1.0", Text)

	def _InspectCanvasPoint(self, Event):
		if self.NormalizedScore is None or self.Score is None or self.Transform is None:
			return
		PixelX = int((Event.x - self.ViewOffsetX) / self.PreviewScale)
		PixelY = int((Event.y - self.ViewOffsetY) / self.PreviewScale)
		if not (0 <= PixelX < self.ImageWidth and 0 <= PixelY < self.ImageHeight):
			return
		WorldX, WorldZ = self.Transform.ToWorld(PixelX, PixelY, self.ImageWidth, self.ImageHeight)

		TraderLines = []
		NearestTrader = None
		NearestTraderDistance = None
		for Trader in self.Traders:
			Distance = math.hypot(PixelX - Trader.PixelX, PixelY - Trader.PixelY) / max(self.Transform.PixelsPerWorldUnit, 0.000001)
			if NearestTraderDistance is None or Distance < NearestTraderDistance:
				NearestTraderDistance = Distance
				NearestTrader = Trader
		if NearestTrader is not None:
			Contribution = max(0.0, float(self.MaxTraderDist.get()) - NearestTraderDistance) * float(self.TraderDistanceCoefficient.get())
			TraderLines.append(f"Trader contribution: {Contribution:.2f}\n")
			TraderLines.append(f"Nearest trader: {NearestTrader.Name}\n")
			TraderLines.append(f"Trader distance: {NearestTraderDistance:.1f} m\n")
		else:
			TraderLines.append("Trader contribution: 0.00; no traders found.\n")

		PrefabContribs = []
		MaxDist = float(self.MaxDistCoeff.get())
		for Prefab in self.ScorePrefabs:
			Distance = math.hypot(PixelX - Prefab.PixelX, PixelY - Prefab.PixelY) / max(self.Transform.PixelsPerWorldUnit, 0.000001)
			Contribution = max(0.0, MaxDist - Distance)
			if Contribution > 0.0:
				PrefabContribs.append((Contribution, Distance, Prefab))
		PrefabContribs.sort(key=lambda Item: Item[0], reverse=True)

		Text = []
		Text.append(f"Pixel: {PixelX}, {PixelY}\n")
		Text.append(f"World E/W X: {WorldX:.1f}\n")
		Text.append(f"World N/S Z: {WorldZ:.1f}\n")
		Text.append(f"Raw score: {float(self.Score[PixelY, PixelX]):.2f}\n")
		Text.append(f"Normalized score: {float(self.NormalizedScore[PixelY, PixelX]):.2f}\n\n")
		Text.extend(TraderLines)
		Text.append("\nPrefab contributions:\n")
		if not PrefabContribs:
			Text.append("  No selected POI inside MaxDistCoeff radius.\n")
		else:
			for Contribution, Distance, Prefab in PrefabContribs[:40]:
				Text.append(f"  +{Contribution:.2f}  dist={Distance:.1f}m  T{Prefab.Tier}  {Prefab.Name}\n")
			if len(PrefabContribs) > 40:
				Text.append(f"  ... {len(PrefabContribs) - 40} more\n")
		self._SetInfoText("".join(Text))


def SanitizePathPart(Text: str) -> str:
	SafeText = re.sub(r"[^A-Za-z0-9._-]+", "_", Text).strip("._-")
	return SafeText or "world"

def ParseWorldPrefabs(PrefabsXmlPath: pathlib.Path) -> list[PrefabPlacement]:
	Root = xml.etree.ElementTree.parse(PrefabsXmlPath).getroot()
	Placements = []
	for Element in Root.iter():
		Attrs = {str(Key): str(Value) for Key, Value in Element.attrib.items()}
		Name = FindFirstAttribute(Attrs, ["name", "prefab", "prefabname", "prefab_name", "filename"])
		PositionText = FindFirstAttribute(Attrs, ["position", "pos", "location"])
		if not Name:
			continue
		Position = ParsePosition(PositionText, Attrs)
		if Position is None:
			continue
		Tier = ParseTierFromAttributes(Attrs)
		Name = NormalizePlacementName(Name)
		Placement = PrefabPlacement(
			Name=Name,
			WorldX=Position[0],
			WorldY=Position[1],
			WorldZ=Position[2],
			Tier=Tier,
			TierSource="prefabs.xml" if Tier is not None else "unknown",
			IsTrader=IsTraderName(Name),
		)
		Placements.append(Placement)
	return Placements



def GetScorePrefabExclusionReason(Placement: PrefabPlacement, MinTier: int, Strict: bool) -> str | None:
	if not Placement.InMap:
		return "outside map bounds after coordinate transform"
	if Placement.IsTrader:
		return "trader prefab is used only for trader distance scoring"
	if Placement.Tier is None:
		return "missing DifficultyTier/quest tier"
	if IsExcludedPrefabName(Placement.Name):
		return "name matches excluded part/tile/decoration pattern"
	if Strict:
		if Placement.Tier <= MinTier:
			return f"tier {Placement.Tier} is not greater than minimum tier {MinTier}"
	else:
		if Placement.Tier < MinTier:
			return f"tier {Placement.Tier} is less than minimum tier {MinTier}"
	return None


def WriteFilteredPoiLog(LogPath: pathlib.Path, FilteredPrefabs: list[tuple[PrefabPlacement, str]]):
	with LogPath.open("w", encoding="utf-8", newline="") as LogFile:
		LogFile.write("Filtered POIs\n")
		LogFile.write("=============" + "\n")
		LogFile.write(f"Total filtered: {len(FilteredPrefabs)}\n\n")
		for Placement, Reason in sorted(FilteredPrefabs, key=lambda Item: (Item[1], Item[0].Name, Item[0].WorldX, Item[0].WorldZ)):
			TierText = "unknown" if Placement.Tier is None else str(Placement.Tier)
			LogFile.write(
				f"Reason: {Reason} | "
				f"Name: {Placement.Name} | "
				f"Tier: {TierText} ({Placement.TierSource}) | "
				f"World: E/W {Placement.WorldX:.2f}, Alt {Placement.WorldY:.2f}, N/S {Placement.WorldZ:.2f} | "
				f"Pixel: {Placement.PixelX}, {Placement.PixelY} | "
				f"InMap: {Placement.InMap}\n"
			)

def FindFirstAttribute(Attrs: dict[str, str], Keys: list[str]) -> str | None:
	LowerToOriginal = {Key.lower(): Key for Key in Attrs.keys()}
	for Key in Keys:
		Original = LowerToOriginal.get(Key.lower())
		if Original is not None:
			return Attrs[Original]
	return None


def ParsePosition(PositionText: str | None, Attrs: dict[str, str]) -> tuple[float, float, float] | None:
	if PositionText:
		Numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", PositionText)
		if len(Numbers) >= 3:
			return float(Numbers[0]), float(Numbers[1]), float(Numbers[2])

	Lower = {Key.lower(): Value for Key, Value in Attrs.items()}
	X = Lower.get("x") or Lower.get("worldx")
	Y = Lower.get("y") or Lower.get("worldy") or "0"
	Z = Lower.get("z") or Lower.get("worldz")
	if X is None or Z is None:
		return None
	try:
		return float(X), float(Y), float(Z)
	except ValueError:
		return None


def ParseTierFromAttributes(Attrs: dict[str, str]) -> int | None:
	for Key, Value in Attrs.items():
		KeyLower = Key.lower().replace("_", "")
		if KeyLower in ["tier", "difficultytier", "questtier", "questdifficultytier"]:
			Tier = ParseIntFromText(Value)
			if Tier is not None:
				return Tier
	return None


def ParseIntFromText(Text: str | None) -> int | None:
	if Text is None:
		return None
	Match = re.search(r"-?\d+", str(Text))
	if not Match:
		return None
	try:
		return int(Match.group(0))
	except ValueError:
		return None


def NormalizePlacementName(Name: str) -> str:
	Name = Name.strip().replace("\\", "/")
	if Name.lower().endswith(".xml") or Name.lower().endswith(".tts"):
		Name = Name.rsplit(".", 1)[0]
	return Name


def NormalizeKey(Name: str) -> str:
	Name = NormalizePlacementName(Name).lower()
	Name = Name.strip("/")
	return Name


def IsTraderName(Name: str) -> bool:
	Lower = Name.lower()
	return "trader" in Lower or "settlement_trader" in Lower


def IsExcludedPrefabName(Name: str) -> bool:
	Lower = NormalizePlacementName(Name).lower()
	for Part in EXCLUDED_PREFAB_NAME_PARTS:
		if Part in Lower:
			return True
	Stem = pathlib.PurePosixPath(Lower).name
	for Prefix in EXCLUDED_PREFAB_PREFIXES:
		if Stem.startswith(Prefix):
			return True
	return False


def BuildPrefabTierIndex(PrefabsFolder: pathlib.Path) -> dict[str, int]:
	Index = {}
	for XmlPath in PrefabsFolder.rglob("*.xml"):
		Tier = ParsePrefabDifficultyTier(XmlPath)
		if Tier is None:
			continue
		StemKey = NormalizeKey(XmlPath.stem)
		Index[StemKey] = Tier
		try:
			Rel = XmlPath.relative_to(PrefabsFolder).with_suffix("")
			RelKey = NormalizeKey(str(Rel).replace(os.sep, "/"))
			Index[RelKey] = Tier
		except ValueError:
			pass
	return Index


def ParsePrefabDifficultyTier(XmlPath: pathlib.Path) -> int | None:
	try:
		Root = xml.etree.ElementTree.parse(XmlPath).getroot()
	except Exception:
		return None
	Tier = ParseTierFromAttributes(Root.attrib)
	if Tier is not None:
		return Tier
	for Element in Root.iter():
		Attrs = {str(Key): str(Value) for Key, Value in Element.attrib.items()}
		Name = FindFirstAttribute(Attrs, ["name"])
		if Name and Name.lower().replace("_", "") in ["difficultytier", "questtier", "questdifficultytier"]:
			Value = FindFirstAttribute(Attrs, ["value"])
			Tier = ParseIntFromText(Value)
			if Tier is not None:
				return Tier
		Tier = ParseTierFromAttributes(Attrs)
		if Tier is not None:
			return Tier
	return None


def ApplyPrefabTiers(Placements: list[PrefabPlacement], PrefabTierIndex: dict[str, int]):
	if not PrefabTierIndex:
		return
	for Placement in Placements:
		if Placement.Tier is not None:
			continue
		Keys = [NormalizeKey(Placement.Name), pathlib.PurePosixPath(NormalizeKey(Placement.Name)).name]
		for Key in Keys:
			Tier = PrefabTierIndex.get(Key)
			if Tier is not None:
				Placement.Tier = Tier
				Placement.TierSource = "Data/Prefabs"
				break


def GetGeneratedWorldSize(WorldPath: pathlib.Path, Placements: list[PrefabPlacement], ImageWidth: int, ImageHeight: int) -> tuple[float, float]:
	MapInfoPath = WorldPath / "map_info.xml"
	WorldSize = ParseWorldSizeFromMapInfo(MapInfoPath)
	if WorldSize is not None:
		return WorldSize

	MaxAbsX = max((abs(Placement.WorldX) for Placement in Placements), default=ImageWidth / 2.0)
	MaxAbsZ = max((abs(Placement.WorldZ) for Placement in Placements), default=ImageHeight / 2.0)
	NeededSize = max(MaxAbsX * 2.0, MaxAbsZ * 2.0, float(ImageWidth), float(ImageHeight))
	CommonSizes = [1024, 2048, 4096, 6144, 8192, 10240, 12288, 16384]
	for Size in CommonSizes:
		if Size >= NeededSize:
			return float(Size), float(Size)
	return float(math.ceil(NeededSize)), float(math.ceil(NeededSize))


def ParseWorldSizeFromMapInfo(MapInfoPath: pathlib.Path) -> tuple[float, float] | None:
	try:
		Root = xml.etree.ElementTree.parse(MapInfoPath).getroot()
	except (FileNotFoundError, xml.etree.ElementTree.ParseError, OSError):
		return None

	Values: dict[str, str] = {}
	for Element in Root.iter():
		Attrs = {str(Key).lower(): str(Value) for Key, Value in Element.attrib.items()}
		Name = Attrs.get("name", "").lower().replace("_", "")
		Value = Attrs.get("value")
		if Name and Value is not None:
			Values[Name] = Value
		for Key, Value in Attrs.items():
			Values[Key.lower().replace("_", "")] = Value

	Size = ParseIntFromText(Values.get("worldgensize") or Values.get("worldsize") or Values.get("size"))
	if Size is not None and Size > 0:
		return float(Size), float(Size)

	Width = ParseIntFromText(Values.get("width") or Values.get("mapwidth"))
	Height = ParseIntFromText(Values.get("height") or Values.get("mapheight"))
	if Width is not None and Height is not None and Width > 0 and Height > 0:
		return float(Width), float(Height)
	return None


def ChooseBestCoordinateTransform(Placements: list[PrefabPlacement], Width: int, Height: int, WorldWidth: float | None = None, WorldHeight: float | None = None) -> CoordinateTransform:
	WorldWidth = float(WorldWidth or Width)
	WorldHeight = float(WorldHeight or Height)
	ScaleX = Width / WorldWidth
	ScaleY = Height / WorldHeight
	Transforms = [
		CoordinateTransform(
			f"center origin, image north up: px=(x+{WorldWidth:g}/2)*{ScaleX:g}, py=({WorldHeight:g}/2-z)*{ScaleY:g}",
			lambda X, Z, W, H, WW=WorldWidth, WH=WorldHeight: ((X + WW / 2.0) * W / WW, (WH / 2.0 - Z) * H / WH),
			lambda PX, PY, W, H, WW=WorldWidth, WH=WorldHeight: (PX * WW / W - WW / 2.0, WH / 2.0 - PY * WH / H),
			(ScaleX + ScaleY) / 2.0,
		),
		CoordinateTransform(
			f"center origin, image south up: px=(x+{WorldWidth:g}/2)*{ScaleX:g}, py=(z+{WorldHeight:g}/2)*{ScaleY:g}",
			lambda X, Z, W, H, WW=WorldWidth, WH=WorldHeight: ((X + WW / 2.0) * W / WW, (Z + WH / 2.0) * H / WH),
			lambda PX, PY, W, H, WW=WorldWidth, WH=WorldHeight: (PX * WW / W - WW / 2.0, PY * WH / H - WH / 2.0),
			(ScaleX + ScaleY) / 2.0,
		),
		CoordinateTransform(
			f"raw origin, image north up: px=x*{ScaleX:g}, py=({WorldHeight:g}-z)*{ScaleY:g}",
			lambda X, Z, W, H, WW=WorldWidth, WH=WorldHeight: (X * W / WW, (WH - Z) * H / WH),
			lambda PX, PY, W, H, WW=WorldWidth, WH=WorldHeight: (PX * WW / W, WH - PY * WH / H),
			(ScaleX + ScaleY) / 2.0,
		),
		CoordinateTransform(
			f"raw origin, image south up: px=x*{ScaleX:g}, py=z*{ScaleY:g}",
			lambda X, Z, W, H, WW=WorldWidth, WH=WorldHeight: (X * W / WW, Z * H / WH),
			lambda PX, PY, W, H, WW=WorldWidth, WH=WorldHeight: (PX * WW / W, PY * WH / H),
			(ScaleX + ScaleY) / 2.0,
		),
	]

	Best = Transforms[0]
	BestInside = -1
	for Transform in Transforms:
		Inside = 0
		for Placement in Placements:
			PX, PY = Transform.ToPixel(Placement.WorldX, Placement.WorldZ, Width, Height)
			if 0 <= PX < Width and 0 <= PY < Height:
				Inside += 1
		if Inside > BestInside:
			BestInside = Inside
			Best = Transform
	return Best


def ComputeScoreMap(
	Width: int,
	Height: int,
	Traders: list[PrefabPlacement],
	ScorePrefabs: list[PrefabPlacement],
	TraderDistanceCoefficient: float,
	MaxTraderDist: float,
	MaxDistCoeff: float,
	PixelsPerWorldUnit: float = 1.0,
	StatusCallback=None,
) -> numpy.ndarray:
	Score = numpy.zeros((Height, Width), dtype=numpy.float32)

	if Traders and MaxTraderDist > 0.0 and TraderDistanceCoefficient != 0.0:
		TraderScore = numpy.zeros((Height, Width), dtype=numpy.float32)
		for Index, Trader in enumerate(Traders):
			if StatusCallback is not None:
				StatusCallback(f"Computing trader distance field {Index + 1}/{len(Traders)}...")
			Contribution = ComputeRadialContribution(Width, Height, Trader.PixelX, Trader.PixelY, MaxTraderDist, PixelsPerWorldUnit)
			if Contribution is not None:
				X0, Y0, Values = Contribution
				Y1 = Y0 + Values.shape[0]
				X1 = X0 + Values.shape[1]
				numpy.maximum(TraderScore[Y0:Y1, X0:X1], Values, out=TraderScore[Y0:Y1, X0:X1])
		Score += TraderScore * numpy.float32(TraderDistanceCoefficient)

	if ScorePrefabs and MaxDistCoeff > 0.0:
		for Index, Prefab in enumerate(ScorePrefabs):
			if StatusCallback is not None and (Index % 5 == 0 or Index + 1 == len(ScorePrefabs)):
				StatusCallback(f"Computing POI fields {Index + 1}/{len(ScorePrefabs)}...")
			Contribution = ComputeRadialContribution(Width, Height, Prefab.PixelX, Prefab.PixelY, MaxDistCoeff, PixelsPerWorldUnit)
			if Contribution is not None:
				X0, Y0, Values = Contribution
				Y1 = Y0 + Values.shape[0]
				X1 = X0 + Values.shape[1]
				Score[Y0:Y1, X0:X1] += Values
	return Score


def ComputeRadialContribution(Width: int, Height: int, CenterX: int, CenterY: int, RadiusFloat: float, PixelsPerWorldUnit: float = 1.0):
	PixelsPerWorldUnit = max(float(PixelsPerWorldUnit), 0.000001)
	Radius = int(math.ceil(RadiusFloat * PixelsPerWorldUnit))
	X0 = max(0, CenterX - Radius)
	X1 = min(Width, CenterX + Radius + 1)
	Y0 = max(0, CenterY - Radius)
	Y1 = min(Height, CenterY + Radius + 1)
	if X0 >= X1 or Y0 >= Y1:
		return None
	Y = numpy.arange(Y0, Y1, dtype=numpy.float32)[:, None]
	X = numpy.arange(X0, X1, dtype=numpy.float32)[None, :]
	Distance = numpy.sqrt((X - numpy.float32(CenterX)) ** 2 + (Y - numpy.float32(CenterY)) ** 2)
	WorldDistance = Distance / numpy.float32(PixelsPerWorldUnit)
	Values = numpy.maximum(numpy.float32(0.0), numpy.float32(RadiusFloat) - WorldDistance).astype(numpy.float32, copy=False)
	return X0, Y0, Values


def NormalizeScore(Score: numpy.ndarray) -> numpy.ndarray:
	MinScore = float(numpy.min(Score))
	MaxScore = float(numpy.max(Score))
	if MaxScore <= MinScore:
		return numpy.zeros_like(Score, dtype=numpy.float32)
	return ((Score - numpy.float32(MinScore)) * numpy.float32(100.0 / (MaxScore - MinScore))).astype(numpy.float32)


def RenderMap(
	NormalizedScore: numpy.ndarray,
	Biomes: numpy.ndarray,
	Traders: list[PrefabPlacement],
	ScorePrefabs: list[PrefabPlacement],
	BiomeBoundaryWidth: int,
) -> Image.Image:
	Clamped = numpy.clip(NormalizedScore, 0.0, 100.0)
	Red = ((100.0 - Clamped) * 2.55).astype(numpy.uint8)
	Green = (Clamped * 2.55).astype(numpy.uint8)
	Blue = numpy.zeros_like(Red, dtype=numpy.uint8)
	Rgb = numpy.dstack([Red, Green, Blue])

	BoundaryMask = ComputeBiomeBoundaryMask(Biomes, BiomeBoundaryWidth)
	Rgb[BoundaryMask] = [0, 0, 0]

	ImageOut = Image.fromarray(Rgb, mode="RGB")
	Draw = ImageDraw.Draw(ImageOut)
	Font = LoadFont(max(12, int(ImageOut.width / 220)))
	SmallFont = LoadFont(max(10, int(ImageOut.width / 330)))

	DrawBiomeLabels(Draw, Biomes, Font)
	DrawMarkers(Draw, Traders, ScorePrefabs, SmallFont)
	DrawLegend(Draw, ImageOut.width, ImageOut.height, Font)
	return ImageOut


def ComputeBiomeBoundaryMask(Biomes: numpy.ndarray, LineWidth: int) -> numpy.ndarray:
	LineWidth = max(1, int(LineWidth))
	if LineWidth % 2 == 0:
		LineWidth += 1
	Boundary = numpy.zeros(Biomes.shape[:2], dtype=bool)
	Horizontal = numpy.any(Biomes[:, 1:, :] != Biomes[:, :-1, :], axis=2)
	Vertical = numpy.any(Biomes[1:, :, :] != Biomes[:-1, :, :], axis=2)
	Boundary[:, 1:] |= Horizontal
	Boundary[:, :-1] |= Horizontal
	Boundary[1:, :] |= Vertical
	Boundary[:-1, :] |= Vertical
	MaskImage = Image.fromarray((Boundary.astype(numpy.uint8) * 255), mode="L")
	MaskImage = MaskImage.filter(ImageFilter.MaxFilter(LineWidth))
	return numpy.asarray(MaskImage) > 0


def LoadFont(Size: int):
	Candidates = [
		"/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
		"/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
		"C:/Windows/Fonts/arialbd.ttf",
		"C:/Windows/Fonts/arial.ttf",
	]
	for Candidate in Candidates:
		if pathlib.Path(Candidate).exists():
			return ImageFont.truetype(Candidate, Size)
	return ImageFont.load_default()


def ColorDistanceSquared(A: tuple[int, int, int], B: tuple[int, int, int]) -> int:
	return sum((int(A[Index]) - int(B[Index])) ** 2 for Index in range(3))


def GuessBiomeName(Color: tuple[int, int, int]) -> str:
	BestName = None
	BestDistance = None
	for Name, KnownColor in KNOWN_BIOME_COLORS:
		Distance = ColorDistanceSquared(Color, KnownColor)
		if BestDistance is None or Distance < BestDistance:
			BestName = Name
			BestDistance = Distance
	if BestDistance is not None and BestDistance <= 80 * 80:
		return BestName
	return f"Biome #{Color[0]:02X}{Color[1]:02X}{Color[2]:02X}"


def DrawBiomeLabels(Draw: ImageDraw.ImageDraw, Biomes: numpy.ndarray, Font):
	Height, Width = Biomes.shape[:2]
	Flat = Biomes.reshape((-1, 3))
	Colors, Counts = numpy.unique(Flat, axis=0, return_counts=True)
	MinPixels = max(64, int(Width * Height * 0.004))
	for ColorArray, Count in sorted(zip(Colors, Counts), key=lambda Item: Item[1], reverse=True)[:16]:
		if Count < MinPixels:
			continue
		Color = tuple(int(Value) for Value in ColorArray)
		Mask = numpy.all(Biomes == ColorArray, axis=2)
		Ys, Xs = numpy.nonzero(Mask)
		if len(Xs) == 0:
			continue
		MeanX = int(numpy.mean(Xs))
		MeanY = int(numpy.mean(Ys))
		SampleStep = max(1, len(Xs) // 20000)
		SampleXs = Xs[::SampleStep]
		SampleYs = Ys[::SampleStep]
		Distances = (SampleXs - MeanX) ** 2 + (SampleYs - MeanY) ** 2
		BestIndex = int(numpy.argmin(Distances))
		LabelX = int(SampleXs[BestIndex])
		LabelY = int(SampleYs[BestIndex])
		Text = GuessBiomeName(Color)
		Draw.text((LabelX, LabelY), Text, fill=(255, 255, 255), font=Font, anchor="mm", stroke_width=3, stroke_fill=(0, 0, 0))


def DrawMarkers(Draw: ImageDraw.ImageDraw, Traders: list[PrefabPlacement], ScorePrefabs: list[PrefabPlacement], Font):
	for Prefab in ScorePrefabs:
		R = 5
		Draw.ellipse((Prefab.PixelX - R, Prefab.PixelY - R, Prefab.PixelX + R, Prefab.PixelY + R), fill=(255, 255, 255), outline=(0, 0, 0), width=2)
	for Trader in Traders:
		R = 8
		Draw.rectangle((Trader.PixelX - R, Trader.PixelY - R, Trader.PixelX + R, Trader.PixelY + R), fill=(0, 128, 255), outline=(255, 255, 255), width=2)
		Draw.text((Trader.PixelX + R + 4, Trader.PixelY), "Trader", fill=(255, 255, 255), font=Font, anchor="lm", stroke_width=2, stroke_fill=(0, 0, 0))


def DrawLegend(Draw: ImageDraw.ImageDraw, Width: int, Height: int, Font):
	Margin = 20
	BarWidth = min(500, max(180, Width // 5))
	BarHeight = 22
	X0 = Margin
	Y0 = Height - Margin - BarHeight - 25
	for X in range(BarWidth):
		T = X / max(1, BarWidth - 1)
		Color = (int((1.0 - T) * 255), int(T * 255), 0)
		Draw.line((X0 + X, Y0, X0 + X, Y0 + BarHeight), fill=Color)
	Draw.rectangle((X0, Y0, X0 + BarWidth, Y0 + BarHeight), outline=(255, 255, 255), width=2)
	Draw.text((X0, Y0 - 4), "0", fill=(255, 255, 255), font=Font, anchor="lb", stroke_width=2, stroke_fill=(0, 0, 0))
	Draw.text((X0 + BarWidth, Y0 - 4), "100", fill=(255, 255, 255), font=Font, anchor="rb", stroke_width=2, stroke_fill=(0, 0, 0))
	Draw.text((X0 + BarWidth / 2, Y0 + BarHeight + 4), "NormalizedPosScore", fill=(255, 255, 255), font=Font, anchor="mt", stroke_width=2, stroke_fill=(0, 0, 0))


def main():
	Root = tkinter.Tk()
	WorldScoreApp(Root)
	Root.mainloop()


if __name__ == "__main__":
	main()
